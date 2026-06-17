# -*- coding: utf-8 -*-
"""v4 市值分层评级选股: 23因子模型 + 公用事业降权"""
import sys, os, warnings
sys.path.insert(0, 'G:/stocks')
os.environ['NO_PROXY'] = '*'
warnings.filterwarnings('ignore')

import numpy as np
import pandas as pd
import lightgbm as lgb
from datetime import timedelta, date


UTILITY_KW = ['电力', '电网', '核电', '水务', '水电', '燃气', '天然气',
              '环保', '供水', '供热', '火电', '热力', '公用']


def main():
    import qlib
    from qlib.data import D
    from config import PROVIDER_URI_MAP, PARQUET_DIR, _HOME
    qlib.init(provider_uri=PROVIDER_URI_MAP, region='cn')

    print("=" * 90)
    print("  v4 市值分层选股 (23因子 + 公用事业降权)")
    print("=" * 90)

    # 预过滤: 9 个 base 字段每个都 >= 60 天, 否则 34-expr 联合计算时某个
    # worker 返回 shape (2,) 与 (~1050,) 广播不兼容 (Qlib joblib bug).
    # csiall 失败时降级 csi500.
    START_TIME = '2022-01-01'
    # END_TIME 动态取今天 — Qlib D.features 会自动截到 bin 实际最大日期.
    # 2026-04-24 前写死过 '2026-04-10' 导致 B3 一直取 04-10 → FinCast parquet
    # 没有 04-10 预测就退化到 LGB. 动态化后自动跟 incremental_update_tushare
    # + fincast_daily_predict 的日线节奏对齐.
    END_TIME = date.today().strftime('%Y-%m-%d')
    BASE_FIELDS = ['$close', '$open', '$high', '$low',
                   '$volume', '$amount', '$pe_ttm', '$pb', '$total_mv']

    def _filter_universe(univ_name):
        try:
            probe_insts = D.instruments(univ_name)
            good = None
            for f in BASE_FIELDS:
                df = D.features(probe_insts, [f],
                                start_time=START_TIME, end_time=END_TIME)
                cnt = df.groupby(level='instrument').size()
                g = set(cnt[cnt >= 60].index.tolist())
                good = g if good is None else (good & g)
            return sorted(good)
        except Exception as ex:
            print(f"  [{univ_name}] 预过滤异常: {ex}", flush=True)
            return None

    univ = 'csiall'
    good_insts = _filter_universe(univ)
    if good_insts and len(good_insts) >= 500:
        print(f"预过滤 csiall: -> {len(good_insts)} 只 (9 base 字段均 >=60 天)",
              flush=True)
        instruments = good_insts
    else:
        print(f"csiall 预过滤不可用, 降级 csi500", flush=True)
        univ = 'csi500'
        good_insts = _filter_universe(univ)
        if good_insts and len(good_insts) >= 200:
            instruments = good_insts
            print(f"预过滤 csi500: -> {len(instruments)} 只", flush=True)
        else:
            instruments = D.instruments(univ)
            print(f"csi500 原 universe 使用", flush=True)

    print("\n训练34因子模型...", flush=True)
    exprs = [
        'Ref($close,5)/$close-1', 'Ref($close,10)/$close-1', 'Ref($close,20)/$close-1',
        'Mean($volume,5)/(Mean($volume,20)+1e-8)', 'Std($close/Ref($close,1)-1,20)',
        'Mean(Abs($close/Ref($close,1)-1)/($volume+1e-8),20)',
        '$close/Mean($close,20)-1', '$close/Mean($close,60)-1',
        'Mean(If($close>Ref($close,1),$close-Ref($close,1),0),14)/(Mean(Abs($close-Ref($close,1)),14)+1e-8)',
        'Mean(($high-$low)/($close+1e-8),20)', 'Mean($amount,5)/(Mean($amount,20)+1e-8)',
        '$close/Mean($close,5)-1', 'Mean($volume,5)/(Mean($volume,60)+1e-8)',
        'Corr($close,$volume,20)', '($close-$low)/($high-$low+1e-8)',
        '$pe_ttm', '$pb', '$total_mv',
        'Std($amount,20)/(Mean($amount,20)+1e-8)',
        'Mean(If($close/Ref($close,1)-1>0.03,1,0),20)',
        'Mean(If($open<Ref($close,1)*0.99,1,0),20)',
        'Mean(If($close>Ref($close,1),$volume,0),20) - Mean(If($close<Ref($close,1),$volume,0),20)',
        'Mean(If($close/Ref($close,1)-1<-0.03,1,0),20) - Mean(If($close/Ref($close,1)-1>0.03,1,0),20)',
        'If($close>=Max($high,20),1,0)',
        'Mean(If($close>=Max($high,5),1,0),20)',
        'If($close/Ref($close,20)-1>0, Ref($close,3)/$close-1, 0)',
        '(1 - Mean($volume,5)/(Mean($volume,20)+1e-8)) * (1 + $close/Ref($close,5)-1)',
        'Min($volume,20) / (Mean($volume,20)+1e-8)',
        'Std(If($close>Ref($close,1),$close/Ref($close,1)-1,0),20) / (Std(If($close<Ref($close,1),$close/Ref($close,1)-1,0),20)+1e-8)',
        'Max($close/Ref($close,1)-1,20)',
        'Max($volume,20) / (Mean($volume,20)+1e-8)',
        'Mean(($high-$low)/($close+1e-8),20)',
        'Mean(If($close>Ref($close,1),$close/Ref($close,1)-1,0),20) / (Mean(If($close<Ref($close,1),Ref($close,1)/$close-1,0),20)+1e-8)',
        'Mean($close/Ref($close,1)-1,60) / (Std($close/Ref($close,1)-1,60)+1e-8)',
        'Ref($close,-6)/Ref($close,-1)-1',
        # 2026-04-24 新增 3 维 A 股专属因子 (tsfm_exp/scripts/ic_probe_new_factors.py 验证通过):
        #   turnover_pct_60: 换手相对 60 日均值偏离 (R27 D45 动态 > 绝对), IC -0.040, ICIR -0.354
        #   ps_ttm: 市销率 (与 rev_20/bias_ma20 Spearman ρ ≈ 0, 独立估值维度), IC -0.034, ICIR -0.280
        #   dv_ttm: 股息率 (防守/价值信号, 弱但正交), IC +0.029, ICIR +0.173
        '$turnover_rate / (Mean($turnover_rate, 60) + 1e-8) - 1',
        '$ps_ttm',
        '$dv_ttm',
    ]
    fn = ['rev_5','rev_10','rev_20','vol_ratio_5_20','volatility_20','amihud_20',
          'bias_ma20','bias_ma60','rsi_approx','avg_amplitude_20','amount_ratio_5_20',
          'bias_ma5','vol_trend_5_60','corr_close_vol_20','close_pos',
          'pe_ttm','pb','total_mv','amt_cv','big_up_freq','gap_dn_freq','obv_slope',
          'big_dn_minus_up','breakout_20','new_high_freq','pullback_3d','quiet_dip',
          'vol_dry','updown_vol_ratio','max_gain_20','vol_spike',
          'price_density_20','win_loss_ratio','stock_sharpe_60',
          'label',
          'turnover_pct_60','ps_ttm_raw','dv_ttm']

    data = D.features(instruments, exprs, start_time=START_TIME, end_time=END_TIME)
    data.columns = fn
    data['log_mv'] = np.log(data['total_mv'].clip(lower=1) + 1)
    data['pe_clip'] = data['pe_ttm'].clip(-200, 500)
    data['ps_clip'] = data['ps_ttm_raw'].clip(-100, 200)  # ps_ttm 同步 clip 防极端值

    # 2026-04-26 新增 ind_turnover (行业平均换手率) — 行业相对强度信号 ────────
    # IC -0.0447 / ICIR -0.314 / +IC 率 38.3%, ρ vs 个股 turnover = 0.42 (独立).
    # 验证脚本: tsfm_exp/scripts/ic_probe_industry_strength.py.
    # 机理: 行业整体换手过热 → 成员股近 5 日 fwd_ret 系统性下行 (与个股
    #       turnover_pct_60 互补, 后者抓个股层面, 这里抓行业层面).
    # 行业映射来自 tushare_stock_basic.parquet (110 一级行业, 99.9% 覆盖).
    try:
        _stk_basic = pd.read_parquet(os.path.join(PARQUET_DIR, 'tushare_stock_basic.parquet'))
        _ind_map = {
            (r.ts_code.split('.')[1] + r.ts_code.split('.')[0])
            if '.' in r.ts_code else r.ts_code: r.industry
            for r in _stk_basic.itertuples()
        }
        _inst = data.index.get_level_values('instrument')
        data['_industry'] = _inst.map(_ind_map)
        # 计算 $turnover_rate 的行业 (date, industry) 平均, 回填到个股
        data['_turnover_raw'] = D.features(
            instruments, ['$turnover_rate'],
            start_time=START_TIME, end_time=END_TIME,
        ).iloc[:, 0]
        _dt = data.index.get_level_values('datetime')
        data['ind_turnover'] = (
            data.groupby([_dt, data['_industry']])['_turnover_raw'].transform('mean')
        )
        data.drop(columns=['_industry', '_turnover_raw'], inplace=True)
        print(f"  [ind_turnover] 行业相对强度因子已加 (38 维), "
              f"覆盖 {data['ind_turnover'].notna().mean():.1%}", flush=True)
    except Exception as e:
        print(f"  [ind_turnover] 计算失败, 退化为 0: {e}", flush=True)
        data['ind_turnover'] = 0.0

    # 2026-04-26 新增 R27 市场宽度残差 broadcast (39 / 40 维) ─────────────────
    # amt_resid_pct60 双向显著: >=0.95 spread +1.54pp/win 72.8% (A/S), <=0.05 -1.45pp.
    # lu_resid_pct60 较弱但保留 (R27 +1.91pp 全样本残差化, 我滚动 60 日略弱).
    # 验证脚本: tsfm_exp/scripts/ic_probe_market_breadth.py.
    # 数据: strategy/research/market_breadth_resid.parquet (1567 日 ~6 年).
    # 同一日所有股票获得同一值, LGB tree 用作市场 regime 条件 split.
    try:
        _br = pd.read_parquet(os.path.join(_HOME, 'strategy', 'research', 'market_breadth_resid.parquet'))
        _dts = data.index.get_level_values('datetime')
        # broadcast: 每只股票按日期取相同的 market resid 值
        for _col in ('lu_resid_pct60', 'amt_resid_pct60'):
            _series = _br[_col].reindex(_dts.unique()).ffill()
            data[_col] = _dts.map(_series)
        print(f"  [market_breadth] R27 残差因子已 broadcast (40 维), "
              f"amt_resid 覆盖 {data['amt_resid_pct60'].notna().mean():.1%}",
              flush=True)
    except Exception as e:
        print(f"  [market_breadth] 加载失败, 退化为 0: {e}", flush=True)
        data['lu_resid_pct60'] = 0.0
        data['amt_resid_pct60'] = 0.0

    mf = [x for x in data.columns if x not in
          ('label', 'pe_ttm', 'pb', 'total_mv', 'ps_ttm_raw')]

    dates = data.index.get_level_values('datetime')
    ld = dates.max()
    train = data[(dates >= '2022-01-01') & (dates <= (ld - timedelta(days=5)).strftime('%Y-%m-%d'))].dropna(subset=['label']).copy()
    pred = data[dates == ld].copy()
    train[mf] = train[mf].fillna(0)
    pred[mf] = pred[mf].fillna(0)

    dt_train = lgb.Dataset(train[mf].values, label=train['label'].values)
    params = {'objective': 'regression', 'metric': 'mse', 'device': 'gpu', 'gpu_use_dp': False,
              'learning_rate': 0.03, 'num_leaves': 128, 'max_depth': 7,
              'subsample': 0.85, 'colsample_bytree': 0.85, 'lambda_l1': 10,
              'lambda_l2': 50, 'min_child_samples': 100, 'verbose': -1}
    model = lgb.train(params, dt_train, num_boost_round=500)
    pred['score'] = model.predict(pred[mf].values)
    print(f"LGB就绪, 预测 {len(pred)} 只", flush=True)

    # ─── B3 集成: LGB + FinCast (w=0.60/0.40, B3 最优 RankICIR+1.0833) ───────
    #    FinCast 日预测由 tsfm_exp/scripts/fincast_daily_predict.py 落地.
    #    没有文件 / 日期不对 / 匹配不足 → 自动退化到纯 LGB, 保证向后兼容.
    pred['score_lgb'] = pred['score'].copy()
    pred['score_fincast'] = np.nan
    b3_active = False
    fc_path = os.path.join(PARQUET_DIR, 'fincast_daily_pred.parquet')
    try:
        if os.path.exists(fc_path):
            fc = pd.read_parquet(fc_path)
            fc_dates = fc.index.get_level_values('eval_date')
            if ld in fc_dates:
                fc_series = fc.xs(ld, level='eval_date')['pred_ret_5d']
                insts = pred.index.get_level_values('instrument')
                pred['score_fincast'] = np.array(
                    [fc_series.get(i, np.nan) for i in insts], dtype=float)
                n_has_fc = int(pred['score_fincast'].notna().sum())
                if n_has_fc >= 50:
                    def _z(s):
                        sd = s.std(skipna=True)
                        if not (sd and sd > 0):
                            return s * 0.0
                        return (s - s.mean(skipna=True)) / (sd + 1e-9)
                    z_lgb = _z(pred['score_lgb'])
                    fc_filled = pred['score_fincast'].fillna(pred['score_fincast'].mean())
                    z_fc = _z(fc_filled)

                    # 2026-04-26 B3 v2: FC 权重单边自适应 (LGB 固定 0.6, FC 在 [0.1, 0.5]) ─
                    # 计算 FC 自身近 20 个交易日的 RankICIR (cross-sectional Spearman),
                    # 高 → 用满 0.4, 低 → 减半甚至 0.1; LGB 是经过验证的强信号 (RankICIR
                    # +0.85) 不需要按短期波动调整. 历史 LGB 预测没落地, 暂只单边自适应.
                    w_fc = 0.4   # 默认 (退化), 与 B3 v1 行为一致
                    fc_icir_recent = None
                    try:
                        # FC 历史预测在 fc DataFrame; 把 fwd_5d label 拼起来
                        # 取近 25 个交易日 (留 5 日做未来收益)
                        recent_dts = sorted(fc_dates.unique())[-25:-5]
                        if len(recent_dts) >= 10:
                            ics = []
                            label_panel = data['label']  # multiindex (inst, date)
                            for dt in recent_dts:
                                fc_sub = fc.xs(dt, level='eval_date')['pred_ret_5d']
                                # 对应日 fwd_5d label (同一天的 label 字段已是 t→t+5 收益)
                                lbl_sub = label_panel.xs(dt, level='datetime', drop_level=False)
                                lbl_sub.index = lbl_sub.index.get_level_values('instrument')
                                joined = pd.concat([fc_sub, lbl_sub], axis=1, join='inner').dropna()
                                if len(joined) < 30:
                                    continue
                                ic_d = joined.iloc[:, 0].rank().corr(joined.iloc[:, 1].rank())
                                if pd.notna(ic_d):
                                    ics.append(ic_d)
                            if len(ics) >= 5:
                                ic_arr = np.asarray(ics, dtype=float)
                                ic_mean = float(ic_arr.mean())
                                ic_std = float(ic_arr.std()) + 1e-9
                                fc_icir_recent = ic_mean / ic_std
                                # logistic 映射: ICIR 0 → 0.25, 0.5 → ~0.4, -0.5 → ~0.1
                                w_fc = float(np.clip(0.4 / (1 + np.exp(-2 * fc_icir_recent)),
                                                     0.1, 0.5))
                    except Exception as e:
                        print(f"[B3v2] FC IC 估计失败 ({e}), 用默认 w_fc=0.4", flush=True)

                    w_lgb = 1 - w_fc
                    pred['score'] = w_lgb * z_lgb + w_fc * z_fc
                    b3_active = True
                    extra = (f"  FC近20日 RankICIR={fc_icir_recent:+.3f}"
                             if fc_icir_recent is not None else "  (默认权重)")
                    print(f"[B3v2] FinCast 自适应启用 w_LGB={w_lgb:.2f} + w_FC={w_fc:.2f}, "
                          f"{n_has_fc}/{len(pred)} 只有 FC 预测.{extra}", flush=True)
                else:
                    print(f"[B3] FinCast 匹配仅 {n_has_fc} 只 < 50, 退化纯 LGB", flush=True)
            else:
                print(f"[B3] FinCast parquet 无 {ld.date()} 预测, 退化纯 LGB", flush=True)
        else:
            print(f"[B3] FinCast 预测文件不存在, 退化纯 LGB "
                  "(提示: python tsfm_exp/scripts/fincast_daily_predict.py)",
                  flush=True)
    except Exception as e:
        print(f"[B3] FinCast 加载失败 ({e}), 退化纯 LGB", flush=True)

    # 自适应因子择时
    TF = ['rev_20', 'amt_cv', 'updown_vol_ratio', 'vol_dry', 'breakout_20', 'stock_sharpe_60', 'vol_20']
    TD = {'rev_20': +1, 'amt_cv': -1, 'updown_vol_ratio': -1, 'vol_dry': +1,
          'breakout_20': -1, 'stock_sharpe_60': -1, 'vol_20': -1}
    recent = sorted(dates[dates < ld].unique())[-20:]
    fw = {}
    for fn in TF:
        if fn not in data.columns: continue
        ics = []
        for rd in recent:
            try:
                dd = data.xs(rd, level='datetime')[[fn, 'label']].dropna()
                if len(dd) >= 30: ics.append(dd[fn].rank().corr(dd['label'].rank()))
            except: pass
        fw[fn] = max(0, np.mean(ics) * TD[fn]) if len(ics) >= 5 else 0
    tw = sum(fw.values())
    if tw > 0: fw = {k: v/tw for k, v in fw.items()}
    else: fw = {k: 1.0/len(TF) for k in TF if k in data.columns}

    ascore = pd.Series(0.0, index=pred.index)
    for fn, w in fw.items():
        if fn not in pred.columns or w == 0: continue
        p = pred[fn].rank(pct=True)
        if TD[fn] < 0: p = 1 - p
        ascore += w * p
    pred['adaptive'] = ascore
    pred['final_score'] = 0.5 * pred['score'].rank(pct=True) + 0.5 * pred['adaptive'].rank(pct=True)

    active_w = {k: v for k, v in fw.items() if v > 0.01}
    print(f"自适应权重: {' | '.join(f'{k}={v:.0%}' for k,v in sorted(active_w.items(), key=lambda x:-x[1]))}")
    if b3_active:
        print(f"最终分 = 50% (LGB 60% + FinCast 40%) + 50% 自适应\n", flush=True)
    else:
        print(f"最终分 = 50% LGB + 50% 自适应\n", flush=True)

    # 名称
    name_map = {}
    try:
        spot = pd.read_parquet(os.path.join(PARQUET_DIR, 'spot_2026-04-03.parquet'))
        for _, row in spot.iterrows():
            c6 = str(row['代码'])
            qc = ('SH' if c6.startswith('6') else 'SZ' if c6.startswith(('0', '3')) else 'BJ') + c6
            name_map[qc] = str(row['名称'])
    except Exception:
        pass

    # 收盘价 + 过滤
    pred['mv_billion'] = pred['total_mv'] / 1e4
    close_latest = D.features(
        list(pred.index.get_level_values('instrument').unique()),
        ['$close'], start_time=(ld - timedelta(days=10)).strftime('%Y-%m-%d'),
        end_time=ld.strftime('%Y-%m-%d'))
    close_latest.columns = ['close']
    close_latest = close_latest.groupby('instrument').last()
    pred = pred.join(close_latest, on='instrument', how='left')

    st_codes = [c for c in pred.index.get_level_values('instrument').unique()
                if 'ST' in name_map.get(c, '')]
    mask = ((pred['mv_billion'] > 30) & (pred['close'] > 3) & (pred['close'] < 500) &
            pred['score'].notna() & (~pred.index.get_level_values('instrument').isin(st_codes)))
    filtered = pred[mask].sort_values('final_score', ascending=False).copy()
    print(f"过滤后: {len(filtered)} 只\n", flush=True)

    # v4 评分
    results = []
    for idx, row in filtered.head(200).iterrows():
        code = idx[0] if isinstance(idx, tuple) else idx
        name = name_map.get(code, '?')
        mv = row['mv_billion']
        utility = any(kw in name for kw in UTILITY_KW)

        if mv >= 1000:
            mv_layer, fc, mc = '大盘', 0, 1
        elif mv >= 300:
            mv_layer, fc, mc = '中盘', 1, 2
        elif mv >= 100:
            mv_layer, fc, mc = '中小', 2, 2
        else:
            mv_layer, fc, mc = '小盘', 2, 2

        # 因子面
        r20p = (pred['rev_20'] < row['rev_20']).mean() if pd.notna(row.get('rev_20')) else 0.5
        acp = (pred['amt_cv'] < row['amt_cv']).mean() if pd.notna(row.get('amt_cv')) else 0.5
        vp = (pred['volatility_20'] < row['volatility_20']).mean() if pd.notna(row.get('volatility_20')) else 0.5
        bp = (pred['big_up_freq'] < row['big_up_freq']).mean() if pd.notna(row.get('big_up_freq')) else 0.5
        fs = 0
        if r20p > 0.7: fs += 1
        if r20p < 0.3: fs -= 1
        if acp < 0.3: fs += 1
        if acp > 0.7: fs -= 1
        if vp < 0.3: fs += 1
        if vp > 0.7: fs -= 1
        if bp < 0.3: fs += 0.5
        if bp > 0.7: fs -= 0.5
        fs = int(max(-fc, min(fc, round(fs))))

        # 技术面
        rsi = row.get('rsi_approx', 0.5)
        ts = 0
        if rsi < 0.3: ts += 1
        if rsi > 0.7: ts -= 1
        b20 = row.get('bias_ma20', 0)
        if b20 < -0.05: ts += 0.5
        if b20 > 0.05: ts -= 0.5
        ts = int(max(-2, min(2, round(ts))))

        # 模型面
        rp = (pred['score'] > row['score']).mean()
        if rp < 0.05: ms = 2
        elif rp < 0.15: ms = 2
        elif rp < 0.3: ms = 1
        elif rp < 0.5: ms = 1
        elif rp < 0.7: ms = 0
        elif rp < 0.85: ms = -1
        else: ms = -2
        ms = max(-mc, min(mc, ms))

        # 量能面
        vs = 0
        vr = row.get('vol_ratio_5_20', 1)
        if vr < 0.7: vs = 1
        elif vr > 1.5: vs = -1

        # 公用事业
        ud = -1 if utility else 0
        total = fs + ts + ms + vs + ud

        results.append({
            'code': code, 'name': name[:8], 'mv': mv, 'layer': mv_layer,
            'close': row.get('close', 0), 'model_score': row['score'],
            'f': fs, 't': ts, 'm': ms, 'v': vs, 'u': ud, 'total': total,
            'utility': utility, 'rev20_pct': r20p, 'amt_cv_pct': acp,
        })

    df = pd.DataFrame(results).sort_values('total', ascending=False)

    # ─── P2.5: 写 per-code 排名 parquet 供 daily_signal_pack 富化 ───────────────
    #   lgb_rank/lgb_pct over 全 pred (close 过滤无关); v4 total/layer from 顶200 df.
    #   纯增量, try/except 包裹, 不拖垮主流程 / 不改 console 输出.
    try:
        _pred_codes = list(pred.index.get_level_values('instrument'))
        _rank = pd.DataFrame({'code': _pred_codes, 'lgb_score': pred['score'].values})
        _rank = _rank.dropna(subset=['lgb_score'])
        _rank['lgb_pct'] = _rank['lgb_score'].rank(pct=True)
        _rank = _rank.sort_values('lgb_score', ascending=False).reset_index(drop=True)
        _rank['lgb_rank'] = range(1, len(_rank) + 1)
        _v4 = df[['code', 'total', 'layer']].rename(
            columns={'total': 'v4_total', 'layer': 'v4_layer'})
        _out = _rank.merge(_v4, on='code', how='left')
        _out['date'] = ld.strftime('%Y-%m-%d') if hasattr(ld, 'strftime') else str(ld)[:10]
        _vp = os.path.join(PARQUET_DIR, 'v4_ranking_latest.parquet')
        _out.to_parquet(_vp, index=False)
        print(f"[v4_ranking] per-code 排名已写 {len(_out)} 行 (顶200 含 v4) -> {_vp}", flush=True)
    except Exception as _e:
        print(f"[v4_ranking] 排名 parquet 写入失败 (不影响主流程): {_e}", flush=True)

    # 输出
    print("=" * 100)
    print("  v4 评级 Top 20")
    print("=" * 100)
    header = (f"{'#':>3} {'代码':<12} {'名称':<10} {'价格':>6} {'市值':>7} {'层':>4} "
              f"{'模型分':>7} {'因子':>4} {'技术':>4} {'模型':>4} {'量能':>4} {'公用':>4} {'总分':>4}")
    print(header)
    print("-" * 100)

    for i, (_, r) in enumerate(df.head(20).iterrows()):
        print(f"{i+1:>3} {r['code']:<12} {r['name']:<10} {r['close']:>6.2f} "
              f"{r['mv']:>5.0f}亿 {r['layer']:>4} {r['model_score']:>+7.4f} "
              f"{r['f']:>+4} {r['t']:>+4} {r['m']:>+4} {r['v']:>+4} {r['u']:>+4} {r['total']:>+4}")

    n_util = df.head(20)['utility'].sum()
    n_sm = (df.head(20)['mv'] < 100).sum()
    n_ms = ((df.head(20)['mv'] >= 100) & (df.head(20)['mv'] < 300)).sum()
    n_md = ((df.head(20)['mv'] >= 300) & (df.head(20)['mv'] < 1000)).sum()
    n_lg = (df.head(20)['mv'] >= 1000).sum()
    print(f"\nTop20: 小盘{n_sm} 中小盘{n_ms} 中盘{n_md} 大盘{n_lg} | 公用{n_util}只 | 均值{df.head(20)['mv'].mean():.0f}亿")

    # 特征重要性
    print(f"\n{'='*70}")
    print("  LightGBM 特征重要性 Top 10")
    print(f"{'='*70}")
    imp = pd.Series(model.feature_importance(importance_type='gain'), index=mf).sort_values(ascending=False)
    for fname, val in imp.head(10).items():
        print(f"  {fname:<25} {val:>10.0f}")
    print("\n完成")


if __name__ == '__main__':
    main()
