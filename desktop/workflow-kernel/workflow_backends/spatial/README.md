# spatial 代码包

**主入口：`run.py`。** 在本目录执行 `python run.py --help` 查看可用命令。

```text
run.py           主运行入口
src/             核心分析实现与公共函数，由主入口调用
scripts/         可单独运行的辅助分析脚本（Python / R）
config/          参数配置与样本表示例
requirements.txt Python 依赖清单
docs/            完整流程、安装说明、方法依据与使用边界
SHA256SUMS.txt   文件完整性校验清单
```

## 开始使用

1. 按 [完整运行说明](docs/WORKFLOW.md) 安装依赖。
2. 修改 `config/` 中的真实路径与样本信息；配置内相对路径以该配置文件所在目录为基准。
3. 从本目录执行主入口。辅助分析命令在完整说明中单列。

```bash
python run.py --help
python run.py analyze --config config/config.example.json
```

配置中的输出目录默认指向本包根目录下的 results/；现有非空输出目录不会被覆盖。
本精简包仅保留运行相关文件和说明，测试材料与真实数据独立保存。

## 2026-10-08 真实数据验收

真实 GSE153424 两张切片，各 192 spot 区域，保留全部基因进入 QC；原生 Visium/H5AD → 空间图、999 次置换 Moran、邻域富集、marker。Zarr 仅验证表导出和计数/坐标守恒；未验证分割和 Zarr 起始完整分析。完整切片 999 次置换计算已中断，不记为通过。

服务器记录：`/home/xzq/wutx/data/Spatial/real_validation_20261008/`。随交付报告：`real_data_validation/真实数据服务器测试报告.md`。这次是功能与输入兼容性验收，不能推断所有平台、物种和规模均已验证。
