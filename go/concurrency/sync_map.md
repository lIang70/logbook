# sync.Map Implementation

`sync.Map` 是 Go 官方提供的并发安全 Map，专为 **读多写少** (Read-Heavy) 场景优化。

## TL;DR

- 它是**读多写少专用结构**，不是“并发版 map”的通用替代：read map 原子读无锁，dirty map 加锁，**性能取决于命中率而不是并发度**。
- 晋升是代价所在：读穿透到 dirty 就 `misses++`，`misses >= len(dirty)` 时把 dirty 提为 read 并重建——**频繁插入新 key 会持续触发复制，反而不如 `RWMutex + map`**。
- 删除是**延迟删除**：只把 entry 置 `nil` / `expunged`，真正的物理清理由下一次晋升/复制完成；所以删除后内存不一定立即下降。
- 常见误用：把 `sync.Map` 当“避免锁”的万能钥匙。**如果每个 key 只被一个 goroutine 访问**（per-connection 状态那样），根本不需要任何并发容器。

## 核心设计思路

通过 **空间换时间**，维护两个 Map：

1. **read map**: 原子操作 (Atomic)，无锁读取。
2. **dirty map**: 普通 Map，需要互斥锁 (Mutex) 保护。

## 结构体

```go
type Map struct {
    mu Mutex
    read atomic.Value // 存储 readOnly 结构体
    dirty map[interface{}]*entry
    misses int // 记录 read map 穿透次数
}

type readOnly struct {
    m       map[interface{}]*entry
    amended bool // true 表示 dirty 中包含 read 中没有的数据
}
```

## 操作流程

### 1. Load (读)

1. 尝试从 `read` map 中读取。
2. 如果没找到，且 `amended` 为 true (表示 dirty 有新数据)：
   - 加锁 `mu`。
   - 二次检查 `read` (Double Check)。
   - 如果还不在 `read`，从 `dirty` 中读取。
   - **Misses 计数**: `misses++`。如果 `misses >= len(dirty)`，触发 **Promotion (晋升)**：将 `dirty` 提升为 `read`，清空 `dirty`，重置 `misses`。

### 2. Store (写/更新)

1. 如果 key 已经在 `read` 中：
   - 尝试通过 atomic CAS 更新值 (无锁)。
2. 如果 key 不在 `read` 中 (或 CAS 失败表示该条目已被标记删除)：
   - 加锁 `mu`。
   - 如果 key 在 `read` 中 (并发导致)：更新 entry。
   - 如果 key 在 `dirty` 中：直接更新。
   - 如果 key 都不在：
     - 如果 `dirty` 为 nil，从 `read` 复制未删除的数据创建新 `dirty`。
     - 将新 key 写入 `dirty`。
     - 设置 `read.amended = true`。

### 3. Delete (删)

- **延迟删除**: 删除操作只是将 entry 的指针置为 `nil` (或特殊标记 `expunged`)。
- 真正物理删除发生在 `dirty` 提升为 `read` 时，或者从 `read` 复制到 `dirty` 时（已删除的不会被复制）。

## 适用场景

- **缓存**: Key 集合稳定，读取频繁。
- **不适用**: 频繁插入新 Key 的场景（会导致频繁的 dirty -> read 晋升和数据复制，性能不如 `RWMutex + map`）。

## 相关链接

- [Go Concurrency Patterns](concurrency_patterns.md)：什么时候该用锁、什么时候该用 channel
- [Channels (通道) Implementation](channels.md)：另一条共享状态的路径（同样有锁）
- [Go Scheduler (GMP 模型)](../runtime/scheduler.md)：goroutine 挂起/唤醒的真实代价
- [Concurrency](../../os/concurrency.md)：内存模型与可见性的底层
- [pprof 与性能读法](../runtime/pprof.md)：mutex profile 看锁竞争
