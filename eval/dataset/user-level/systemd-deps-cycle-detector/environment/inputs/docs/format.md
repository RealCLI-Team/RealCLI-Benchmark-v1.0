# deps.txt 与产出文件说明

## deps.txt 每行格式

```
<unit>: After=<a,b,...> Requires=<c,d,...>
```

- `unit` 是 systemd 单元名，冒号后允许空白
- `After=` 与 `Requires=` 两个字段都可以缺省
- 字段值用逗号分隔，允许空白，允许该字段整体不存在
- 每个 unit 的依赖前驱 = After 集合 ∪ Requires 集合
- 空行忽略

## outputs/deps.dot

- 必须是合法 digraph，顶层写 `digraph deps {` 开头，`}` 结尾
- 节点集合等于 deps.txt 中出现过的全部 unit 名（包含只作为依赖出现的 unit）
- 每条边写作 `"dep" -> "unit";`，方向是 依赖 -> 被依赖者
- 节点声明与边可以任意顺序，但节点名必须用双引号包裹

## outputs/cycles.json

顶层是对象：

```
{
  "cycles": [
    ["app-a.service", "app-b.service", "app-c.service"],
    ...
  ]
}
```

- 无环时 `cycles` 为 `[]`
- 有环时每个环是一个成员数组，按“字典序最小的成员作为起点”旋转后给出
- 环内顺序沿有向边方向（dep -> unit）
- 同一个环只出现一次
- 同一强连通组内的每条基本环（elementary/simple cycle）各占一条，含双向依赖构成的 2 元环。
