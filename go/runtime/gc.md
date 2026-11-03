# GC

## TL;DR

- Go 的 GC 是**并发三色标记清除**，STW 只出现在标记准备与标记终止两处；停顿主要是**扫栈**的代价，所以它跟 goroutine 数量相关，不单纯跟堆大小相关。
- **混合写屏障（1.8+）是并发标记能成立的前提**：赋值**之前**先把旧值和新值都着色——旧值管删边、新值管插入边，三个地方容易写反：判据是「槽位对象黑 + 被引用对象白」、旧值也要 shade、屏障必须在赋值前。
- **非分代 + 非压缩**：对象又多又小时扫描贵，且堆会碎片化但**不会移动对象**——所以指针少、对象少比“调大 GOGC”更有效。
- `GOGC` 是「堆增长率」不是「内存上限」；要硬上限得用 `GOMEMLIMIT`（1.19+），两者语义不同，混用会让延迟变成镒齿。
- 排查顺序：`GODEBUG=gctrace=1` 看周期 → pprof 看分配来源 → 最后才谈调参。

## 定位与算法类型

Go 使用并发, 三色标记清除的垃圾回收器.

- 主要目标
  - 低延迟: STW (Stop-The-World) 时间短
  - 高吞吐量: 不影响程序执行效率
  - 并发执行: 与用户代码同时运行

- 算法类型
  - 并发标记清除 (Concurrent Mark-Sweep)
  - 三色标记法 (Tri-color Marking)
  - 非分代 (Non-generational)
  - 非压缩 (Non-compacting)

## 三色标记法

三色标记法将对象分为三种类型: 白色 (尚未访问的对象), 灰色 (已访问但引用的对象需要检查), 黑色 (已访问且引用对象已经检查).

- GC 开始的时候所有对象都是白色
- 从根对象 (全局变量, 栈对象) 开始检查, 并且标记为灰色
- 然后递归标记所有引用

### 完整的周期

- 清扫终止 (Sweep Termination) - STW
- 标记准备 (Mark Setup) - STW：开写屏障、扫描栈根
- 并发标记 (Concurrent Marking)
- 标记终止 (Mark Termination) - STW
- 并发清除 (Concurrent Sweeping)

### 写屏障

用于在并发标记过程中, 确保新创建的引用不会丢失, 防止存活的对象被回收

```go
// 伪代码：混合写屏障（Go 1.8+）——先着色，再赋值
func writePointer(slot **Object, ptr *Object) {
    if gcPhase == marking {
        shade(*slot) // 槽位里的旧值：可能是删边，必须保活
        shade(ptr)   // 新值：可能是插入边，必须保活
    }
    *slot = ptr
}
```

> 三个容易写反的地方：判据是**槽位所属对象为黑且被引用对象为白**（不是拿新值当黑）；shade 的除了新值还有**旧值**（删边保护）；屏障必须在**赋值之前**执行。

## GC 触发机制

1. 堆大小触发
2. 定时触发
3. 手动触发

### 参数调优

```bash
# 环境变量控制
export GOGC=100        # 默认值，增长100%后触发GC
export GOGC=off        # 完全关闭GC
export GODEBUG=gctrace=1  # 输出GC跟踪信息
```

## 相关链接

- [Write Barriers](write_barrier.md)：写屏障的两种着色与「赋值前执行」的语义
- [Go Memory Allocator](memory_allocator.md)：对象从哪来、走哪一级缓存（扫描成本的上游）
- [Go Scheduler](scheduler.md)：扫栈停顿与 P / G 的关系
- [pprof 与性能读法](pprof.md)：从 profile 定位「谁在分配」
- [Memory Management](../../os/memory.md)：分页、虚拟内存、TLB 等底层机制
- [Escape Analysis](../language/escape_analysis.md)：让对象根本不上堆的那一步
