# 本地 IPC 的工程语义 (Unix Domain Socket)

> `os/process.md` §4 列的是"有哪几种 IPC"（管道 / 信号 / 共享内存 / 消息队列），`os/io.md` 讲的是网络 I/O 模型。本篇是中间那层：**两个本机进程之间那条连接，在工程上到底有哪些坑**。
>
> 服务化之后最容易出事的一层不是数据库，是这层：socket 文件权限错了 = 本机任何用户都能连；stale socket 没处理 = 重启永远失败；传了 fd 没关 = 句柄泄漏到进程死。

## TL;DR

- **socket 文件是文件系统里的一个 inode**，权限位就是访问控制。`bind` 时 mode = `0777 & ~umask`（常见 0755），而**连接方需要的是写位**：0755 只有 owner 能连，只有 umask 002/000 得到的 `0775`/`0777` 才是同组/所有人可连。正因为 umask 不可控，**listen 之后必须显式 `chmod`**。
- **对端身份只能靠内核给**：Linux 用 `SO_PEERCRED`，BSD/macOS 用 `getpeereid` / `LOCAL_PEERCRED`。协议里客户端自报的 uid 是攻击者可控的字段，不是身份。
- **stale socket 是重启杀手**：进程被 `kill -9` 后 socket 文件留在磁盘上，`bind` 直接 `EADDRINUSE`。判据是"连一下"——`ECONNREFUSED` 说明没人听（可安全删除），连上说明还活着（别删）。
- **`SCM_RIGHTS` 传的 fd 是共享同一个 open file description**：收发双方都能用，**共享文件偏移**，谁都不能替对方关。传错方向就是"你关了它还在用"。
- **`AF_UNIX` 在容器/namespace 里没有跨边界语义**（文件路径在，监听者不在），需要跨机器就得换成 TCP + 认证——换过去那一刻，本机任意进程都能连了。

## 1. 为什么这层值得单独一篇

网络 socket 出了事你能想到"抓包、看端口"，本地 socket 出了事通常表现为"某个功能时好时坏"，排查方向完全错。

它同时踩了四件事：**文件系统权限**、**进程生命周期**、**内核连接语义**、**文件描述符归属**。任何一件没想清楚，症状都是"偶发"。

## 2. 权限：socket 文件的 mode 就是 ACL

`bind(2)` 创建 socket 文件时用 `0777 & ~umask`，一般 umask 022 → **0755**。但**能不能连看的是写位**（Linux `unix_find_other()` 走 `inode_permission(MAY_WRITE)`，FreeBSD `unp_connect()` 走 `VOP_ACCESS(VWRITE)`）：0755 的 other 只有 `r-x`，第三方用户**连不上**；真正暴露的是 umask 002/000 产生的 `0775` / `0777`。

正确做法是**两步都要做**：

```go
ln, err := net.Listen("unix", path)
if err != nil { /* ... */ }
if err := os.Chmod(path, 0o600); err != nil { /* 关掉 listener，清理目录 */ }
```

只做 `chmod` 不做错误处理同样不行——`chmod` 失败说明权限没落下，此时继续服务等于裸奔。

配套的两条经验：

- **目录权限比文件权限更重要**：`0700` 目录 + `0600` 文件是标准组合（`core/secret/keychain` 就是"每个 secret 一个 0600 文件放在 0700 目录下"）。只有文件权限而目录是 0755，别人删不掉也改不了，但可以遍历枚举。
- **`umask` 是不可信的**：调用方的 umask 可能是 0。显式 `chmod` 是唯一可靠的方式。

> 判据：`ls -l` 看 socket 的 mode，**重点看写位**。`srwxrwxrwx`（所有人）与 `srwxrwxr-x`（同组）是真实的可连暴露面；`srwxr-xr-x` 只有 owner 能连——但它能出现本身就说明 umask 不可控，仍应显式 `chmod 0600`。

## 3. 身份：内核给的和对方说的不是一回事

协议里带 `uid` 字段是**声明**，`SO_PEERCRED` 才是**事实**。

- **Linux**：`getsockopt(SO_PEERCRED)` → `ucred{pid, uid, gid}`，在 `connect` 时刻由内核填写。
- **macOS / BSD**：`getpeereid(2)` 或 `LOCAL_PEERCRED`，只有 uid/gid；macOS 拿不到 pid，FreeBSD 另有 `LOCAL_PEERPID`——**不跨平台，所以跨平台代码不能依赖 pid**。
- **抽象命名空间**（Linux `sun_path[0] == '\0'`）：不落文件系统、进程退出自动消失，**因此没有文件权限可依**——访问控制只能靠 `SO_PEERCRED`，或者干脆不用抽象 socket。

三条实践结论：

- 用 uid 做**粗粒度门禁**（"只有我的用户能连"），不要用它做细粒度授权（同一个 uid 下的进程不隔离）。
- **想区分是哪个进程，别依赖 pid**：pid 会复用，且 BSD 拿不到。把"进程身份"做成连接建立后的一次握手（带 nonce、由服务端校验），pid 只作为日志线索。
- 特权操作（管理员面）用**独立 socket + 独立权限**，不要和主 socket 混用同一个监听。`cap-sandbox` / `cap-workspace` / `platformd` 分成多个 ABI 面就是为了这个。

## 4. 生命周期：stale socket 与原子替换

### 4.1 stale socket

`kill -9`、OOM kill、panic 退出——socket 文件都还在，但已经没人监听。下次启动 `net.Listen("unix", path)` 直接 `EADDRINUSE`。

**不要在启动时无条件 `os.Remove(path)`**。两个进程同时启动时，后一个会把前一个正在用的 socket 文件删掉，然后在同一个路径上 `bind` 成功——于是**两个进程都认为自己独占了这个服务**，客户端连到的却是新的那个。

正确顺序：

```text
if 文件存在:
    conn = connect(path)
    if 连接成功        -> 有活着的监听者, 放弃启动(或退出)
    if ECONNREFUSED    -> 没人听, 是残留, unlink 后 bind
    if EACCES          -> 权限不对, 报错退出(不要删别人的 socket)
```

这个探测**仍有竞态**（探测和 unlink 之间对手可能起来），所以再用一把 `flock` 锁住目录，或者把整个"探测 + unlink + bind"放进持锁区间。

### 4.2 原子替换（热重启）

想要"新进程起来、旧进程还能把手上的请求做完"，可以**先 bind 到临时路径，再 `rename(2)` 覆盖目标路径**（同一文件系统内原子）。新连接会连到新 socket，旧连接仍然指着旧 inode，自然排空。

代价：你要处理"两个进程同时在跑"的一切问题（都读同一个状态目录、都持有排他资源）。**能靠 SIGHUP/重载配置解决的，不要靠换进程解决**（`core/runtime` 的 reload/generation 就是这个思路：换实现，不换进程）。

## 5. 传 fd：`SCM_RIGHTS` 的归属语义

传 fd 的用途很多（把已打开的连接交给 worker、把日志文件交给子进程、传 memfd）。语义上有三个容易踩的点：

- **接收方拿到的是一个新的 fd，指向同一个 open file description**：两边读写共享同一个文件偏移。发一个 fd 过去、两边各写一次，内容会互相穿插。
- **谁都不能替对方关**：发送方 `close` 自己的 fd 不影响接收方；接收方漏关就是句柄泄漏。传完之后"何时关闭"必须有明确约定——通常约定是"接收方负责最终关闭"。
- **cmsg 缓冲区要按 `CMSG_SPACE` 算**：一次传多个 fd 时缓冲区算小会静默截断（`MSG_CTRUNC`），表现是"偶尔少一个 fd"。收到 `MSG_CTRUNC` 必须当错误处理，不能继续用。

> 反直觉：fd passing **不保证跨 namespace / 跨安全边界有效**。它传递的是"在我这个进程上下文里能访问的东西"，接收方权限不足时拿到 fd 也读不了（或在某些 LSM 配置下直接被拒）。

## 6. 连接语义：half-close、backlog、首字节分流

- **half-close 是协议信号，不是异常**：客户端 `shutdown(SHUT_WR)`（Go 里 `conn.(*net.UnixConn).CloseWrite()`）表示"我说完了"，服务端 `Read` 返回 `io.EOF`。把 EOF 当错误处理会导致"正常结束的连接被记为失败"。
- **accept 之后不能做阻塞工作**：accept loop 里做解析/落盘/等锁，backlog 会堆满。注意 AF_UNIX 的 backlog **不受 `somaxconn` clamp**（那只作用于 `inet_listen`），堆满的表现是 connect 阻塞 / `EAGAIN` / 超时，**不是 `ECONNREFUSED`**（那个含义是"根本没有监听者"）。表现是"高峰期随机连不上"。
- **一个 socket 上分流多个协议**：靠**首字节**判断（SOCKS5 是 `0x05`，HTTP 是 `G`/`P` 等 ASCII）——`netproxy` 就是这么在同一个 unix socket 上同时服务 SOCKS5 与 HTTP 代理的。省一个监听，代价是"两个协议的生命周期绑在一起"，且代理中间件（如 TLS 探测）会变复杂。
- **超时是必须的**：本机 socket 也会被卡死的客户端拖住。至少设读超时 / idle 超时（`http.Server` 的 `ReadHeaderTimeout` / `IdleTimeout`），否则一个 debug 时 `nc` 连上不发的终端就能占住一个 goroutine。

## 7. 跨平台：什么时候退回 TCP loopback

`AF_UNIX` 在 Windows 上有（Win10 1803+），但路径限制多、没有 unix 权限语义、Go 生态支持不齐。所以常见做法是**平台分支**：

| 方案              | 谁能连                     | 权限控制                 | 代价                         |
| :---------------- | :------------------------- | :----------------------- | :--------------------------- |
| `AF_UNIX` 0600    | 同 uid 的进程              | 文件 mode                | 跨平台差、容器内路径要挂进去 |
| `TCP 127.0.0.1:0` | **本机任意进程、任意用户** | **没有**，靠应用层 token | 端口冲突、暴露面             |

> **换到 loopback 的那一刻，你失去了全部传输层访问控制**。所以 TCP loopback 必须配一个启动时生成的高熵 token（写进只读的临时文件或环境变量），并在第一帧校验。没做这一步的"本地回环所以安全"是错误直觉——本机其他用户进程照样能连。

容器里还有一层：socket 文件挂进去了，但**监听进程在另一个 mount namespace 里**时行为会很怪。要么把 socket 放进共享 volume，要么老老实实用 TCP。

## 8. 观测与排错

- `ss -x -p` 列 unix socket 与所属进程（比 `netstat` 准）。
- `lsof -U` 看谁持有它（macOS 上更常用）。
- `ls -l` 看 mode / owner / 是否还有链接（删掉的 socket 可能仍有进程持有，`lsof +L1` 能查到）。
- `ECONNREFUSED` vs `ENOENT` vs `EACCES` 是三种完全不同的故障：没人监听 / 路径不存在 / 权限不对。日志里要分开记，别统一成 "connect failed"。

## 9. 常见反模式

- **不 `chmod` 就上线**：默认 0755 虽然只有 owner 能连，但调用方 umask 一旦是 002/000 就变成同组/所有人可连，而你看不出来。
- **启动时无条件 `unlink`**：造出两个进程同时"独占"的假象。
- **用协议里的 uid 做鉴权**：客户端可以随便填。
- **传 fd 后两边都关**：偶发 `EBADF`，或者更糟——关掉的是别人刚复用的 fd 编号。
- **accept loop 里做业务**：高峰期随机连不上。
- **把 EOF 当错误**：half-close 被记成失败，指标失真。
- **loopback 不加 token**：以为本机就安全。

## 10. 上线前自检

- [ ] socket 文件显式 `chmod`（目录 0700 + socket 0600），失败即中止启动
- [ ] stale socket 处理：先 `connect` 探测，`ECONNREFUSED` 才删；有 `flock` 或等价互斥
- [ ] 身份校验走 `SO_PEERCRED` / `getpeereid`，不依赖协议自报字段
- [ ] 传 fd 的路径明确"谁负责关"，并处理 `MSG_CTRUNC`
- [ ] 有读超时 / idle 超时；accept loop 不阻塞
- [ ] 跨平台退回 TCP loopback 时带了随机 token + 首帧校验
- [ ] 日志区分 `ECONNREFUSED` / `ENOENT` / `EACCES`
- [ ] 多个 ABI 面（主 / 沙箱 / 密钥 / 检查点）各自独立 socket 与权限

## 相关链接

- [Process](process.md)：IPC 的教科书分类、进程状态与信号——本篇是它的工程下半场
- [File System](filesystem.md)：socket 文件的 inode / 权限位 / 链接计数从哪来
- [I/O Management](io.md)：网络 I/O 模型（本篇只讲本机 socket 特有部分）
- [沙箱与进程隔离](sandbox.md)：`cap-sandbox` / `cap-workspace` / `platformd` 的多 ABI 面拆法
- [沙箱与权限执行](../ai/harness/sandbox.md)：把同一套权限思维落到 agent 的工具执行边界
- [Craft ABI 与插件宿主](../go/architecture/craft_abi.md)：ABI 面怎么切、谁 owner 哪个 socket
- [Agent 生命周期与 Harness 承载](../ai/harness/lifecycle.md)：进程隔离、spawn 与终止语义（IPC 的对偶问题）
