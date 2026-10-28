# OMaaX 桌面图标

2026-10-28 当前版本：在原始螺旋参考图上做桌面图标适配，保留主体造型、粉蓝配色与绘画纹理，清除矩形背景及螺旋间背景，整理边缘，添加透明安全留白。

范围仅限桌面图标。侧栏、关于页、登录页、使用引导、favicon 和原启动动画保持原样；Android/iOS 图标不变。没有打包或安装。

- `omaax-original.png`：原始参考图留档。
- `omaax-desktop-adapted.png`：当前透明图标母版，1024 × 1024。
- `../src-tauri/icons/` 顶层 PNG、ICO、ICNS：当前桌面应用图标。
- `../build/icon*` 和 `../build/omaax-desktop-icon.svg`：Electron 桌面构建的对应资源；SVG 为嵌入位图的容器。

当前生成提示词、输出、桌面预览、校验和本轮修改前备份在 `../../provenance/pending/logo-desktop-adapted-20261028/`。原图直接缩放及全界面替换的旧截图仅是过程记录，以本轮 `validation.json` 和 `desktop-adapted-preview.png` 为准。

格式转换脚本为本轮目录的 `export-assets.mjs`。平台图标生成器应输出到临时目录，仅复制顶层桌面文件，不覆盖移动平台、界面或启动资源。
