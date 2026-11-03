# Escape Analysis (逃逸分析)

逃逸分析是 Go 编译器在编译阶段进行的一项分析，用于决定变量应该分配在 **栈 (Stack)** 上还是 **堆 (Heap)** 上。

## TL;DR

- 判据只有一条：**变量是否在函数返回后仍被引用**——返回指针、存进全局/容器、被闭包捕获、长度运行时才知道，这四类才上堆。
- **「传 `interface{}` 就逃逸」是不准的**：根因是**装箱**（值要放进 `eface.data` 就需要可寻址存储），而不是“动态派发”；调用点能内联/去虚拟化时，`-m` 会直接报 `does not escape`。
- **2KB 初始栈不是逃逸判据**：真正决定的是“是否外传”与“是否超过编译器栈变量上限”（`maxStackVarSize` 10MB / `maxImplicitStackVarSize` 64KB）。
- 工具只有一条命令：`go build -gcflags="-m -l"`——**必须带 `-l`**，否则内联会把逃逸结果盖掉，你看到的是“优化后”的假象。
- 优化顺序：预分配 → 小对象传值代替指针 → 热点去 interface；**不要为了逃逸而牺牲可读性**，先用 pprof 证明这里是热点。

## 核心原则

- **栈 (Stack)**: 分配和回收极快（指针移动），不需要 GC 参与。函数返回时自动销毁。
- **堆 (Heap)**: 需要 GC 扫描和回收，开销较大。

**编译器的目标**: 尽可能将变量分配在栈上，只有当变量在函数返回后仍被引用（“逃逸”出函数作用域），才分配到堆上。

## 常见逃逸场景

### 1. 指针逃逸

函数返回局部变量的指针。

```go
func create() *User {
    u := User{Name: "test"}
    return &u // u 逃逸到堆上，因为返回值在外部被引用
}
```

### 2. 接口类型逃逸 (Boxing)

接口值本身是 16 字节的 `(type, data)`；逃逸的根因不是"动态派发"，而是**装箱**：具体值类型要放进 `data` 就得有可寻址的存储，值被外传时只能上堆。

```go
func print(v interface{}) {
    fmt.Println(v) // v 已经是接口值，这里不再"装箱"；它本身逃逸是因为被传到别的函数
}

func box(t T) { var i interface{} = t; sink(i) } // 这里才是一次真正的装箱
```

> 判据看调用点：能内联/去虚拟化时 `-m` 会报 `does not escape`；跨包、存进容器或全局时才会看到 `escapes to heap`。

### 3. 栈变量上限与"外传"

goroutine 初始栈是 2KB，但栈会长；**决定逃逸的不是 2KB**，而是两件事：变量是否被外传（返回、存进全局/接口/闭包），以及是否超过编译器的栈变量上限（`maxStackVarSize` 10MB、`maxImplicitStackVarSize` 64KB）。

```go
func huge() {
    s := make([]int, 10000) // 80KB：s 不外传就仍可能在栈上
    _ = s
}

func leak() []int {
    s := make([]int, 10000); return s // 外传 → 堆
}
```

### 4. 闭包引用

闭包中引用的外部变量，如果闭包生命周期超过了函数，变量会逃逸。

### 5. 变量大小不确定

```go
func makeSlice(n int) {
    s := make([]int, n) // 长度 n 不确定，逃逸到堆
}
```

## 分析工具

使用编译参数查看逃逸分析结果：

```bash
# -m: 打印优化决策 (包括逃逸分析)
# -l: 禁用内联 (可选，为了看清特定函数的逃逸情况)
go build -gcflags="-m -l" main.go
```

**输出示例**:

```text
./main.go:5:6: moved to heap: u
./main.go:9:13: ... argument does not escape
```

## 优化建议

1. **多传值，少传指针**: 对于小对象（如基本类型、小结构体），传值复制的开销往往小于 GC 的开销。
2. **预分配**: `make` 切片或 Map 时，如果长度确定，尽量使用常量，避免动态扩容和大小不确定导致的逃逸。
3. **小心 interface{}**: 热点代码路径尽量避免使用 `interface{}`。

## 相关链接

- [Escape Analysis 案例与性能优化](escape_analysis_cases.md)：真实代码里的逃逸形状与修复前后对比
- [Go Memory Allocator](../runtime/memory_allocator.md)：逃逸之后对象落到哪一级缓存
- [GC](../runtime/gc.md)：上堆的代价从这里算（扫描 + 回收）
- [pprof 与性能读法](../runtime/pprof.md)：先证明这里是热点，再动手
- [桌面 agent 的性能治理](../engineering/optimization.md)：减少分配的实战回合
