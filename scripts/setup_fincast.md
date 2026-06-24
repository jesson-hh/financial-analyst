# FinCast 港移 setup(一次性)

FinCast 生成栈(模型代码 + 4GB 权重)体积大、为外部资产,gitignore 不入库。新机/重置后按此还原:

## 1. 模型代码(FinCast-fts)
    git clone https://github.com/vincent05r/FinCast-fts vendor/fincast_repo
  或从既有机器拷:`cp -r G:/stocks/tsfm_exp/fincast_repo vendor/fincast_repo`
  需提供 `vendor/fincast_repo/src/tools/inference_utils.py`(get_model_api)+ ffm/data_tools。

## 2. 权重(v1.pth · 3.97GB)
    放到 vendor/models/fincast/v1.pth
  从既有机器:`cp G:/stocks/tsfm_exp/models/fincast/v1.pth vendor/models/fincast/v1.pth`

## 3. GPU 解释器
  用 conda stocks 环境跑(已装 torch cu128 + FinCast 依赖):
    D:/app/miniconda/envs/stocks/python.exe scripts/fincast_predict.py --date <总市值覆盖日>

## 刷新 DL 参与(日常)
    D:/app/miniconda/envs/stocks/python.exe scripts/fincast_predict.py --date <D>
    python -m guanlan_v2.strategy.compute.regen <D>
  然后重启 9999(刷 LRU)。
