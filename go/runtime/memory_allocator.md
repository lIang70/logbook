# Go Memory Allocator (内存分配器)

Go 的内存分配器基于 Google 的 **TCMalloc** (Thread-Caching Malloc) 算法，核心目标是**减少锁竞争**和**提高内存分配效率**。

## TL;DR

- 三级结构：**mcache（绑 P，无锁）→ mcentral（全局，要加锁）→ mheap（唯一直接向 OS 要内存）**；分配的快慢只取决于“这次走了哪一级”。
- 小对象（16B~32KB）走 mcache 是**无锁路径**——所以性能问题几乎不在“分配器慢”，而在“分配次数多”和“指针多导致 GC 扫描贵”。
- 三档分法：tiny（<16B，**只收 noscan**，合并进同一个 16B 块）/ small（mcache）/ large（>32KB，直接 mheap，**可能触发 GC**）。
- 常见误解：mspan 大小是 `npages × 8KB`，**不是固定 8KB**；一个 mspan 只服务一个 size class。
- 代码层面的抓手永远是**减少分配**（逃逸分析 + 复用 + 预分配），调 size class 不是你能控制的事。

## 核心组件

Go 的内存管理采用层级结构：

### 1. mspan (内存块)

- 内存管理的基本单元。
- 一个 mspan 包含连续的 `npages` 个 page（1 page = 8KB，所以 mspan 大小 = npages × 8KB，不是固定 8KB）。
- 按照对象大小（Size Class）划分，每个 mspan 只分配特定大小的对象。

### 2. mcache (线程缓存)

- **绑定到 P (Processor)**: 每个 P 都有一个本地的 mcache。
- **无锁访问**: 因为绑定到 P，且同一时间只有一个 G 在 P 上运行，所以访问 mcache 不需要加锁，速度极快。
- 包含所有 Size Class 的 mspan 链表。

### 3. mcentral (中心缓存)

- **全局共享**: 所有 P 共享。
- 当 mcache 内存不足时，向 mcentral 申请 mspan。
- **需要加锁**: 访问时需要互斥锁。
- 每个 Size Class 都有两个 mspan 链表：
  - `partial`: 有空闲空间的 mspan。
  - `full`: 已满的 mspan。

### 4. mheap (堆)

- **全局唯一**: 管理整个 Go 程序的虚拟内存。
- 向操作系统申请一大块内存（Arena）。
- 当 mcentral 内存不足时，向 mheap 申请。
- 只有 mheap 会直接与操作系统交互。

## 分配策略

Go 根据对象大小采用不同的分配策略：

1. **微对象 (Tiny Object) < 16B**:
    - 使用专门的 `tiny` 分配器。
    - 多个微对象会被合并到一个 16B 的内存块中，减少碎片。
    - tiny 分配器**只**接受 noscan 且 < 16B 的对象（也接受小于 16B 的 string），合并进同一个 16B 块。

2. **小对象 (Small Object) 16B ~ 32KB**:
    - 也是最常见的分配场景。
    - 直接从 P 本地 `mcache` 对应的 Size Class 的 `mspan` 中分配。
    - **无锁，极快**。

3. **大对象 (Large Object) > 32KB**:
    - 跳过 mcache 和 mcentral。
    - 直接从 `mheap` 申请。
    - 可能会触发 GC。

## 内存分配流程总结

1. `new(T)` 或 `&T{}`。
2. 判断对象大小。
3. 若是小对象 -> `mcache` (无锁)。
4. 若 `mcache` 缺页 -> `mcentral` (有锁)。
5. 若 `mcentral` 缺页 -> `mheap` (有锁)。
6. 若 `mheap` 不足 -> `OS` (系统调用)。

## 相关链接

- [Escape Analysis](../language/escape_analysis.md)：决定对象到底上不上堆的那一步（比任何分配器调优都值）
- [Escape Analysis 案例与性能优化](../language/escape_analysis_cases.md)：真实代码里的逃逸形状
- [GC](gc.md)：分配出来的对象怎么被扫、被回收
- [pprof 与性能读法](pprof.md)：从 heap / alloc profile 定位分配热点
- [Memory Management](../../os/memory.md)：分页与虚拟内存（mheap 之下）
- [桌面 agent 的性能治理](../engineering/optimization.md)：减少分配的实战回合
