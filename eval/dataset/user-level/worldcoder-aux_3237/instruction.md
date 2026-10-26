使用 Three.js 在地球上展示城市与航线网络。

【实现方式（必须严格遵守）】
交付物是单个完全自包含的 index.html：评测 grader 在完全离线的沙箱中用无头 Chromium 只加载这一个文件，因此禁止任何外部网络引用——不得使用指向公网的 importmap、<script src="https://…"> 或任何 CDN 链接。Three.js 风格的场景（地球球体、城市点、航线弧线、飞行动画、轨道控制器式拖拽旋转）必须全部内联实现在这一个文件中（内联的 Three.js 兼容实现或等价的自包含 WebGL 代码均可）。

【数据】
(1) 硬编码恰好 20 个城市：经纬度 + 名称 + continent 字段（如 Asia/Europe/Americas 等）。
(2) 硬编码恰好 30 条航线：每条连接两个城市索引或 id。

【渲染】
(3) 球体表示地球（可纹理或纯色）。
(4) 城市为球面上的点 Mesh。
(5) 航线为 QuadraticBezierCurve3（或等价）抬升弧线的 TubeGeometry/Line，避免穿模球内。
(6) 沿航线循环飞行动画（小飞机/光点），同时存在多条 activeFlights。

【筛选】
(7) id="continentFilter"：select 或下拉，按大洲过滤显示的航线与城市（"all" 表示全部）。

【交互】
(8) 点击航线或图例可选中一条航线，详情面板（可用 id="routeDetail"）。

【状态暴露（必须）】
window.__3D_STATE__ = {
  cityCount: number,
  routeCount: number,
  selectedRoute: null | number | string,
  activeFlights: number,
  filterContinent: string
}

cityCount=20，routeCount=30；filterContinent 与 UI 一致。

输出单个 HTML 文件，浏览器直接打开即可运行。

## DOM contract (required for grading)

Use exactly these DOM element ids/selectors — the grader locates elements by these literal selectors (case-sensitive). Extra elements are fine; missing these means they score zero.

- `#continentFilter`
- `#routeDetailBody`
- `#routeDetailEmpty`

## Output (required)
Write your complete solution as a single self-contained file named exactly `index.html` at the workspace root (i.e. `index.html`, not `archery.html` or any other name). The grader loads `index.html` and nothing else; any other filename scores zero.
