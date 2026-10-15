使用 Three.js 在地球上展示城市与航线网络。

【Three.js 引入方式（必须严格遵守）】
请在 <head> 中使用以下 importmap，并在 <script type="module"> 中通过 ES Module 方式导入：
<script type="importmap">
{ "imports": {
    "three": "https://cdn.jsdelivr.net/npm/three@0.170.0/build/three.module.js",
    "three/addons/": "https://cdn.jsdelivr.net/npm/three@0.170.0/examples/jsm/"
} }
</script>
<script type="module">
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
</script>

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
