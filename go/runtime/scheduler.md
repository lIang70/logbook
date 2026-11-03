# Go Scheduler (GMP 模型)

Go 的调度器 (Scheduler) 是运行时系统的核心，负责将成千上万个 Goroutine (G) 分配到少量的系统线程 (M) 上执行。

## TL;DR

- GMP 的分工一句话：**G 是协程，M 是内核线程，P 是调度上下文（数量 = `GOMAXPROCS`），且 M 必须持有 P 才能跑 G**——后面所有现象都从这句推。
- 三个机制解决三类问题：**work stealing** 解决负载不均（本地队列空 → 看全局队列 → 随机偷别人一半）、**hand off** 解决 syscall 阻塞（M 卡住就释放 P，让别的 M 接手）、**异步抢占**解决饿死（1.14+：sysmon 发现单 G 跑超 10ms 就发 `SIGURG`）。
- 1.14 之前是**协作式抢占**：只在函数调用时检查，**死循环抢不进来**；所以“纯计算死循环 + 小 GOMAXPROCS”能把整个程序挂住。
- `sysmon` 是不绑 P 的后台线程：检测死锁、每 2 分钟强制 GC、抢占长任务、收回被 syscall 卡太久的 P。
- 工程抓手：**goroutine 泄漏看 goroutine profile；syscall 卡顿看 hand off；容器里要警惕 `GOMAXPROCS` 与 cgroup CPU quota 不一致**（老版本只按核数算）。

## 核心模型: GMP

### G (Goroutine)

- **用户级线程**。
- 包含：栈、指令指针 (PC)、调度信息 (等待谁、被谁等待)。
- 初始栈极小 (2KB)，可动态扩缩。
- 状态：`_Grunning`, `_Grunnable`, `_Gwaiting`, `_Gsyscall`, `_Gdead`.

### M (Machine / Thread)

- **操作系统内核线程**。
- 真正执行指令的实体。
- M 必须持有 P 才能执行 G。
- 如果 M 陷入系统调用（如文件 I/O）时间过长，它会释放 P，让 P 去寻找新的 M 继续执行其他 G。

### P (Processor)

- **逻辑处理器 / 调度上下文**。
- 数量由 `GOMAXPROCS` 决定，通常等于 CPU 核数。
- 维护了一个 **本地运行队列 (Local Run Queue)**，存放待执行的 G。
- 充当 M 和 G 之间的中介，解耦了 G 和 M。

## 调度策略

### 1. Work Stealing (工作窃取)

- 当 P 的本地队列为空时，它不会闲着。
- 它会尝试从 **全局运行队列 (Global Run Queue)** 获取 G。
- 如果全局队列也空，它会随机从 **其他 P 的本地队列** “偷” 一半的 G 过来执行。
- **目的**: 负载均衡。

### 2. Hand Off (P 的传递)

- 当 M 执行 G 发生阻塞系统调用（如 syscall）时：
  - M 会释放 P。
  - P 会寻找一个新的 M (或新建一个 M) 来接管自己，继续执行本地队列里的其他 G。
  - 旧的 M 继续阻塞，直到系统调用返回。

### 3. Preemption (抢占式调度)

- **基于协作 (旧版)**: 早期 Go 依赖函数调用时检查栈来实现抢占，死循环无法被抢占。
- **基于信号 (Go 1.14+)**: 真正的异步抢占。
  - `sysmon` 监控线程会检测运行时间过长 (>10ms) 的 G。
  - 向该 M 发送 `SIGURG` 信号。
  - 中断处理函数将当前 G 挂起，放入全局队列，让出 P。

## 调度流程图解

```text
Global Queue (Lock)
    [G5, G6]
       ^
       | (Steal / Fetch)
       |
    +-----+            +-----+
    |  P1 |            |  P2 |
    +-----+            +-----+
    | LRQ |            | LRQ |
    |[G1] |            |[G3] |
    |[G2] |            |[G4] |
    +-----+            +-----+
       |                  |
    +-----+            +-----+
    |  M1 |            |  M2 |
    +-----+            +-----+
      Running            Running
        G1                 G3
```

## sysmon (System Monitor)

一个特殊的后台线程，不需要绑定 P。

- **职责**:
  - 检查死锁。
  - 强制 GC (每2分钟)。
  - 抢占长时间运行的 G。
  - 收回因 syscall 阻塞过久的 P。

## 相关链接

- [GC](gc.md)：STW、栈扫描与 sysmon 的强制 GC
- [netpoll (Go 网络 I/O 模型)](netpoll.md)：网络阻塞不会占住 M，这是 hand off 的另一半
- [Write Barriers](write_barrier.md)：并发标记与 mutator 的交汇点
- [pprof 与性能读法](pprof.md)：goroutine / block / mutex profile 的读法
- [Channels (通道) Implementation](../concurrency/channels.md)：G 在 channel 上的阻塞与唤醒
- [调度器对比](../../os/scheduler_comparison.md)：CFS / BFS 等与 Go 调度器的对照
- [Concurrency](../../os/concurrency.md)：内存模型与同步原语的底层
