// console-recommendation-card.jsx — Phase 10 · 编排选股推荐卡(左栏折叠卡,window 全局互见,
// 无 import)。挂在 console-rail.jsx 计划人审卡旁,不新建页面(UI 只填充不重建)。
// 折叠/轮询/清理照 console-plan-approval-card.jsx 范式:挂载拉一次(折叠头要显最新盘日期+条数),
// open 时 60s 轮询,卸载/收起清理定时器。
// 数据面:GET /orchestration/pipeline/screening/latest(唯一来源)。
// 红线:只显示服务端真值——绝无客户端重排序/重打分/乐观态;评级逐字来自服务端(其上游逐字
//        来自已提交 ResearchPlan@1);降级车道由服务端 join 命名,前端绝不补猜。
//        免责横幅逐字显示服务端 advisory_banner,绝不改写。
//        无推荐 → 「暂无编排推荐」诚实空态;后端 503(未接线)→ 诚实「未接线」,绝不假装有推荐。
function WwRecommendationCard() {
  const [open, setOpen] = React.useState(false);
  const [slate, setSlate] = React.useState(null);   // null=未拉/读取中或无推荐;{...}=最新推荐盘
  const [loaded, setLoaded] = React.useState(false); // 首拉是否已返回(区分「读取中」与「暂无」)
  const [wired, setWired] = React.useState(true);    // 后端 503 → false(诚实空态)

  const API = (window.WW && window.WW.API) || '';

  const pull = React.useCallback(() => {
    fetch(API + '/orchestration/pipeline/screening/latest')
      .then(r => r.json().then(d => ({ s: r.status, d })))
      .then(({ s, d }) => {
        setLoaded(true);
        if (s === 503 || (d && d.ok === false)) { setWired(false); setSlate(null); return; }
        setWired(true); setSlate((d && d.slate) || null);
      }).catch(() => { setLoaded(true); setSlate(null); });
  }, [API]);

  // 挂载拉一次:折叠头也要能显最新盘日期+条数。
  React.useEffect(() => { pull(); }, [pull]);

  // 展开期间 60s 轮询;收起/卸载清理定时器。
  React.useEffect(() => {
    if (!open) return;
    let dead = false;
    const tick = () => { if (!dead) pull(); };
    tick();
    const t = setInterval(tick, 60000);
    return () => { dead = true; clearInterval(t); };
  }, [open, pull]);

  const dateOf = (iso) => (iso || '').slice(0, 10);
  const headline = !loaded ? '' :
    !wired ? '未接线' :
    slate ? (dateOf(slate.as_of) + ' · ' + (slate.entries || []).length + ' 条') : '暂无';
  // 该 entry 的终端降级徽章(服务端徽章逐字判读,绝不重推导)。
  const entryDegraded = (en) =>
    ((slate && slate.badges) || []).indexOf('lane_terminal_degraded:' + en.lane_index) >= 0;

  return (
    <div style={{ marginTop: 8, borderTop: '1px dashed var(--line)', paddingTop: 6 }}>
      <div onClick={() => setOpen(o => !o)} style={{ padding: '9px 13px', borderBottom: open ? '1px solid var(--line-soft)' : 'none', display: 'flex', alignItems: 'baseline', gap: 8, cursor: 'pointer', userSelect: 'none' }}>
        <span className="serif" style={{ fontSize: 12.5, fontWeight: 600 }}>编排推荐 ✦</span>
        <span className="mono" style={{ fontSize: 9, color: (slate && wired) ? 'var(--dai)' : 'var(--ink-3)' }}>{headline}</span>
        <span style={{ flex: 1 }} />
        <span className="mono" style={{ fontSize: 9, color: 'var(--ink-3)' }}>{open ? '▾' : '▸'}</span>
      </div>

      {open && <div style={{ maxHeight: 380, overflowY: 'auto', padding: '8px 13px' }}>
        {!loaded && <div className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }}>读取中…</div>}
        {loaded && !wired && <div className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }}>recommendation surface not wired — 后端未接线(诚实空态)</div>}
        {loaded && wired && !slate && <div className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }}>暂无编排推荐</div>}

        {slate && <div>
          {/* 免责横幅:服务端 advisory_banner 逐字显示,永远在任何内容之前 */}
          <div className="serif" style={{ fontSize: 10.5, color: 'var(--zhu)', borderLeft: '2px solid var(--zhu)', paddingLeft: 7, marginBottom: 8, whiteSpace: 'pre-wrap' }}>{slate.advisory_banner || ''}</div>
          <div className="mono" style={{ fontSize: 8.5, color: 'var(--ink-3)', marginBottom: 6, wordBreak: 'break-all' }}>
            批次 {(slate.batch_id || '').slice(0, 10)}… · as_of {dateOf(slate.as_of)} · 归档 {slate.archive_id}(按候选盘数据日归档)
          </div>

          {(slate.entries || []).map(en => (
            <div key={en.lane_index + ':' + en.code} style={{ display: 'flex', alignItems: 'baseline', gap: 7, padding: '5px 0', borderBottom: '1px dashed var(--line-soft)' }}>
              <span className="mono" style={{ fontSize: 11.5, fontWeight: 600, color: 'var(--ink)' }}>{en.code}</span>
              <span className="serif" style={{ fontSize: 11.5, color: 'var(--dai)' }}>{en.rating}</span>
              {entryDegraded(en) && <span className="mono" title={'lane_terminal_degraded:' + en.lane_index} style={{ fontSize: 8.5, color: 'var(--zhu)', border: '1px solid var(--line-soft)', borderRadius: 4, padding: '0 5px' }}>终端降级</span>}
              <span style={{ flex: 1 }} />
              <span className="mono" style={{ fontSize: 8.5, color: 'var(--ink-3)' }}>lane {en.lane_index}</span>
            </div>
          ))}
          {(slate.entries || []).length === 0 && <div className="mono" style={{ fontSize: 10, color: 'var(--ink-3)', padding: '4px 0' }}>本批次没有任何车道产出已提交的研判产物(无推荐=诚实结果)。</div>}

          {(slate.degraded || []).length > 0 && <div style={{ marginTop: 6 }}>
            <div className="mono" style={{ fontSize: 9, color: 'var(--ink-3)' }}>降级车道(未产出已提交研判,诚实降级非静默丢弃):</div>
            {(slate.degraded || []).map(dg => (
              <div key={'dg' + dg.lane_index} className="mono" style={{ fontSize: 9.5, color: 'var(--ink-2)', padding: '2px 0' }}>
                lane {dg.lane_index} · {dg.code || '(该批次记录缺失,未知标的)'}
              </div>
            ))}
          </div>}

          {(slate.badges || []).length > 0 && <div style={{ marginTop: 6, display: 'flex', flexWrap: 'wrap', gap: 4 }}>
            {(slate.badges || []).map(b => (
              <span key={b} className="mono" style={{ fontSize: 8, color: 'var(--ink-3)', border: '1px solid var(--line-soft)', borderRadius: 4, padding: '0 5px' }}>{b}</span>
            ))}
          </div>}
        </div>}
      </div>}
    </div>
  );
}
window.WwRecommendationCard = WwRecommendationCard;
