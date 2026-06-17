# -*- coding: utf-8 -*-
"""guanlan 自有后端 · seats(席位 / 落子)。

落子模块的 guanlan 自有 REST(随 cards 先例,挂在薄壳 ``create_app`` 上)。
当前只提供**日线真 K**(``/seats/daily``,读 stock_data 经引擎 loader →
``get_data_paths``),供「复盘」在真实价格上逐 bar 推演。其余证据层
(量化因子 / 研报 / 市场 regime)仍为 mock,待上游板块(因子 / 经验卡 / 研报)
形成各自接口后再接 —— 见 ``ui/seats/README.md`` 的「开放项」。
"""
