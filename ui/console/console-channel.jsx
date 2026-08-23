// console-channel.jsx — 帷幄会话的只读出站通道界面。
//
// 这个文件只消费后端已脱敏的 status/public_details。Token 仅在
// WwSecureTokenCard 的局部 password state 中短暂存在，不进入对话、投递行、
// 抽屉状态或浏览器存储。

const WW_CHANNEL_VIEW_COPY = Object.freeze({
  awaiting_token: {
    label: 'Telegram · 待配置', shortLabel: '待配置', tone: 'setup', attention: true,
  },
  awaiting_pairing: {
    label: 'Telegram · 等待配对', shortLabel: '等待配对', tone: 'waiting', attention: true,
  },
  pairing_pending: {
    label: 'Telegram · 配对请求待确认', shortLabel: '待确认', tone: 'attention', attention: true,
  },
  active: {
    label: '已连接 Telegram', shortLabel: '已连接', tone: 'active', attention: false,
  },
  retrying: {
    label: 'Telegram · 重试中', shortLabel: '重试中', tone: 'waiting', attention: true,
  },
  uncertain: {
    label: 'Telegram · 投递待确认', shortLabel: '投递待确认', tone: 'attention', attention: true,
  },
  needs_reconnect: {
    label: 'Telegram · 需要重新连接', shortLabel: '需重连', tone: 'danger', attention: true,
  },
  paused: {
    label: 'Telegram · 已暂停', shortLabel: '已暂停', tone: 'paused', attention: false,
  },
  none: {
    label: '', shortLabel: '', tone: 'none', attention: false,
  },
});

function wwChannelPublicText(value, limit) {
  if (value === null || value === undefined) return '';
  const text = String(value).replace(/[\u0000-\u001f\u007f]/g, ' ').trim();
  // 界面再做一次防御：即使后端 DTO 将来回归，也不渲染 Bot Token 形状。
  if (/^\d{5,16}:[A-Za-z0-9_-]{20,}$/.test(text)) return '';
  return text.slice(0, Math.max(0, Number(limit) || 256));
}

function wwChannelBotLabel(status) {
  const bot = status && status.bot && typeof status.bot === 'object' ? status.bot : {};
  const displayName = wwChannelPublicText(bot.display_name, 128);
  const username = wwChannelPublicText(bot.username, 64).replace(/^@/, '');
  return displayName || (username ? '@' + username : '');
}

function wwChannelViewState(status) {
  const source = status && typeof status === 'object' ? status : {};
  const available = source.available === true;
  const statusUnavailable = source.transient_status_error === true || !!source.status_error;
  if (!available && statusUnavailable) {
    return {
      key: 'status_unavailable', available: false, visible: true,
      label: 'Telegram · 状态暂不可达', shortLabel: '状态未知',
      tone: 'danger', attention: true, connected: false, botName: '',
      statusUnavailable: true,
    };
  }
  const key = available && Object.prototype.hasOwnProperty.call(
    WW_CHANNEL_VIEW_COPY, source.view_state
  ) ? source.view_state : 'none';
  const copy = WW_CHANNEL_VIEW_COPY[key] || WW_CHANNEL_VIEW_COPY.none;
  const botName = wwChannelBotLabel(source);
  const recipient = source.recipient && typeof source.recipient === 'object'
    ? source.recipient : null;
  const hasLiveRecipient = !!(recipient && recipient.user_id);
  const baseLabel = key === 'active' && botName
    ? '已连接 Telegram · ' + botName : copy.label;
  const exactLabel = statusUnavailable && baseLabel
    ? baseLabel + ' · 状态暂不可达' : baseLabel;
  return {
    key,
    available,
    visible: available && key !== 'none',
    label: exactLabel,
    shortLabel: copy.shortLabel,
    tone: statusUnavailable ? 'danger' : copy.tone,
    attention: statusUnavailable || copy.attention,
    connected: hasLiveRecipient && (key === 'active' || key === 'paused' || key === 'retrying'
      || key === 'uncertain' || key === 'needs_reconnect'),
    botName,
    statusUnavailable,
  };
}

function wwChannelFormatTime(value) {
  const safe = wwChannelPublicText(value, 64);
  if (!safe) return '';
  const date = new Date(safe);
  if (Number.isNaN(date.getTime())) return safe;
  try {
    return new Intl.DateTimeFormat('zh-CN', {
      month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
    }).format(date);
  } catch (_) {
    return safe;
  }
}

function wwChannelUiSafeFailure(result, fallback) {
  const code = result && typeof result === 'object'
    ? wwChannelPublicText(result.reason_code || result.error_code, 96) : '';
  const copy = {
    action_ticket_required: '安全确认已过期，请重试。',
    action_ticket_invalid: '安全确认已失效，请重试。',
    session_archived: '该对话已归档，恢复后才能管理通道。',
    channel_unavailable: '通道暂时不可用，请稍后重试。',
    telegram_credential_invalid: 'Bot Token 未通过校验，请重新填写。',
    delivery_not_resolvable: '这条投递状态已变化，请刷新后再处理。',
    channel_request_stale: '当前对话已经切换，请返回原对话核对真实状态。',
  };
  return copy[code] || fallback;
}

function WwChannelStatusBadge({ status, onOpen, compact = false }) {
  const view = wwChannelViewState(status);
  if (!view.visible) return null;
  const className = 'ww-channel-status ww-channel-status--' + view.tone
    + (compact ? ' ww-channel-status--compact' : '');
  const content = compact ? (
    <>
      <span className="ww-channel-status-dot" aria-hidden="true" />
      <span className="ww-channel-sr-only">{view.label}</span>
    </>
  ) : (
    <>
      <span className="ww-channel-status-dot" aria-hidden="true" />
      <span className="ww-channel-status-label">{view.label}</span>
    </>
  );
  if (typeof onOpen === 'function') {
    return <button type="button" className={className} title={view.label}
      aria-label={compact ? view.label : undefined} onClick={onOpen}>{content}</button>;
  }
  return <span className={className} title={view.label}>{content}</span>;
}

function WwSecureTokenCard({ status, draftId, disabled = false, onSubmit, onSuccess }) {
  const statusDraft = status && status.draft && typeof status.draft === 'object'
    ? status.draft.draft_id : '';
  const safeDraftId = wwChannelPublicText(draftId || statusDraft, 160);
  const [token, setToken] = React.useState('');
  const [busy, setBusy] = React.useState(false);
  const [feedback, setFeedback] = React.useState(null);
  const reactId = React.useId();
  const inputId = 'ww-channel-token-' + String(reactId).replace(/[^A-Za-z0-9_-]/g, '');
  const helpId = inputId + '-help';

  React.useEffect(() => {
    // 切换草稿时丢弃上一个局部秘密，不将它提升给父组件。
    setToken('');
    setFeedback(null);
  }, [safeDraftId]);

  const submit = async (event) => {
    if (event && typeof event.preventDefault === 'function') event.preventDefault();
    if (busy || disabled) return;
    const secret = token.trim();
    setBusy(true);
    setFeedback(null);
    try {
      if (!safeDraftId) {
        setFeedback({ ok: false, text: '请先让 Agent 创建连接草稿。' });
        return;
      }
      if (!/^\d{5,16}:[A-Za-z0-9_-]{20,}$/.test(secret)) {
        setFeedback({ ok: false, text: '请检查 Bot Token 格式后重试。' });
        return;
      }
      if (typeof onSubmit !== 'function') {
        setFeedback({ ok: false, text: '安全提交通道尚未就绪。' });
        return;
      }
      const result = await onSubmit(safeDraftId, secret);
      if (!result || result.ok !== true) {
        setFeedback({
          ok: false,
          text: wwChannelUiSafeFailure(result, 'Token 提交没有完成，请重试。'),
        });
        return;
      }
      setFeedback({ ok: true, text: 'Token 已安全提交，正在验证 Bot。' });
      if (typeof onSuccess === 'function') await onSuccess();
    } catch (_) {
      setFeedback({ ok: false, text: '安全提交暂时失败，请稍后重试。' });
    } finally {
      // 无论成功、服务端拒绝或网络异常，都立即清空局部 Token。
      setToken('');
      setBusy(false);
    }
  };

  return (
    <section className="ww-channel-token-card" aria-label="Telegram Bot Token 安全提交">
      <div className="ww-channel-token-seal" aria-hidden="true">钥</div>
      <div className="ww-channel-token-main">
        <div className="ww-channel-eyebrow">仅本次安全提交</div>
        <h3 className="ww-channel-token-title">Telegram Bot Token</h3>
        <p className="ww-channel-token-copy" id={helpId}>
          Token 不会进入对话、历史记录或浏览器存储；提交结束后输入框会立即清空。
        </p>
        <form className="ww-channel-token-form" onSubmit={submit}>
          <label className="ww-channel-token-label" htmlFor={inputId}>Bot Token</label>
          <div className="ww-channel-token-field-row">
            <input id={inputId} className="ww-channel-token-input"
              type="password" autoComplete="off" spellCheck={false} inputMode="text"
              aria-describedby={helpId} value={token}
              disabled={disabled || busy} onChange={event => setToken(event.target.value)}
              placeholder="仅在此处填写" />
            <button className="ww-channel-primary-button" type="submit"
              disabled={disabled || busy || !token.trim()}>
              {busy ? '安全提交中…' : '安全提交'}
            </button>
          </div>
        </form>
        {feedback && <div role="status" aria-live="polite"
          className={'ww-channel-feedback ww-channel-feedback--' + (feedback.ok ? 'ok' : 'error')}>
          {feedback.text}
        </div>}
      </div>
    </section>
  );
}

function WwChannelActionCard({
  status, archived = false, onOpen, onSubmitToken, onRefresh,
}) {
  const view = wwChannelViewState(status);
  if (!view.visible) return null;
  if (view.key === 'awaiting_token') {
    return <WwSecureTokenCard status={status}
      disabled={archived || view.statusUnavailable}
      onSubmit={onSubmitToken} onSuccess={onRefresh} />;
  }

  const copy = {
    awaiting_pairing: {
      title: 'Bot 已验证，等待 Telegram 私聊',
      body: '请私聊该 Bot。Bot 返回临时配对码后，回到当前对话交给 Agent。',
    },
    pairing_pending: {
      title: '已发现配对请求',
      body: '把完整配对码交给 Agent，核对 Telegram 用户后再确认永久绑定。',
    },
    active: {
      title: view.label,
      body: '当前对话的可见文字与 Agent 最终回复会按顺序镜像到已绑定私聊。',
    },
    retrying: {
      title: '投递正在自动恢复',
      body: '已保留发送顺序，暂时不需要重复操作。',
    },
    uncertain: {
      title: '有一条投递需要你判断',
      body: 'Telegram 可能已收到内容，但本机没有拿到确切回执。请在通道管理中选择处理方式。',
    },
    needs_reconnect: {
      title: 'Telegram 连接需要处理',
      body: '已停止盲目重试，对话历史仍完整保留。请打开通道管理查看恢复选项。',
    },
    paused: {
      title: 'Telegram 镜像已暂停',
      body: '暂停期间不会产生新投递；恢复前不会自行补发对话。',
    },
  }[view.key];
  if (!copy) return null;

  return (
    <section className={'ww-channel-action-card ww-channel-action-card--' + view.tone}
      aria-live={view.attention ? 'polite' : undefined}>
      <div className="ww-channel-action-mark" aria-hidden="true">
        {view.attention ? '!' : '电'}
      </div>
      <div className="ww-channel-action-main">
        <div className="ww-channel-eyebrow">Telegram 只读出站通道</div>
        <h3 className="ww-channel-action-title">{copy.title}</h3>
        <p className="ww-channel-action-copy">{copy.body}</p>
        {archived && <p className="ww-channel-readonly-note">
          该对话已归档，当前仅能查看；恢复对话后才能管理通道。
        </p>}
      </div>
      {typeof onOpen === 'function' && <button type="button"
        className="ww-channel-secondary-button" onClick={onOpen}>
        通道管理
      </button>}
    </section>
  );
}

const WW_CHANNEL_DELIVERY_COPY = Object.freeze({
  pending: { label: '待发送', tone: 'waiting' },
  sending: { label: '发送中', tone: 'waiting' },
  retry_wait: { label: '等待重试', tone: 'waiting' },
  sent: { label: '已送达', tone: 'active' },
  uncertain: { label: '结果待确认', tone: 'attention' },
  terminal: { label: '已停止', tone: 'danger' },
  cancelled: { label: '已取消', tone: 'paused' },
});

function wwChannelDeliveryFailure(errorClass) {
  const code = wwChannelPublicText(errorClass, 96);
  const copy = {
    uncertain: '请求结果无法确认',
    credential_invalid: 'Bot 凭证已失效',
    credential_unavailable: 'Bot 凭证暂时不可用',
    target_unavailable: 'Telegram 接收者暂时不可达',
    poll_conflict: '此 Bot 正在被另一处使用',
    rate_limited: 'Telegram 要求稍后再试',
    invalid_media_delivery: '图片未通过安全校验',
  };
  return copy[code] || (code ? '发送没有完成' : '');
}

function WwDeliveryRow({ delivery, disabled = false, onResolve, onRefresh }) {
  const item = delivery && typeof delivery === 'object' ? delivery : {};
  const state = Object.prototype.hasOwnProperty.call(WW_CHANNEL_DELIVERY_COPY, item.state)
    ? item.state : 'pending';
  const view = WW_CHANNEL_DELIVERY_COPY[state];
  const outboxId = wwChannelPublicText(item.outbox_id, 160);
  const [confirmAction, setConfirmAction] = React.useState('');
  const [busy, setBusy] = React.useState(false);
  const [feedback, setFeedback] = React.useState('');
  const kindLabel = item.kind === 'media' ? '图片' : '对话';
  const time = wwChannelFormatTime(item.sent_at || item.created_at);
  const failure = wwChannelDeliveryFailure(item.error_class);
  const attempts = Number.isInteger(item.attempts) && item.attempts > 1 ? item.attempts : 0;
  const actions = state === 'uncertain' ? [
    ['assume_sent', '视为已送达并继续'],
    ['resend', '重新发送并接受可能重复'],
    ['drop', '放弃本条并继续'],
  ] : state === 'terminal' ? [
    ['retry', '修复后重试'],
    ['drop', '放弃本条并继续'],
  ] : [];
  const selected = actions.find(row => row[0] === confirmAction);

  React.useEffect(() => {
    setConfirmAction('');
    setFeedback('');
    setBusy(false);
  }, [outboxId, state]);

  const resolve = async () => {
    if (!selected || busy || disabled || !outboxId || typeof onResolve !== 'function') return;
    setBusy(true);
    setFeedback('');
    try {
      const result = await onResolve(outboxId, selected[0]);
      if (!result || result.ok !== true) {
        setFeedback(wwChannelUiSafeFailure(result, '这条投递暂未处理，请重试。'));
        return;
      }
      // 不在本地把状态写成 sent/pending；等服务端状态刷新。
      setConfirmAction('');
      setFeedback('处理请求已提交，正在更新真实投递状态。');
      if (typeof onRefresh === 'function') await onRefresh();
    } catch (_) {
      setFeedback('处理请求暂时失败，请稍后重试。');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="ww-channel-delivery-row">
      <div className="ww-channel-delivery-head">
        <span className="ww-channel-delivery-kind">{kindLabel}</span>
        <span className={'ww-channel-delivery-state ww-channel-delivery-state--' + view.tone}>
          {view.label}
        </span>
        {time && <time className="ww-channel-delivery-time">{time}</time>}
      </div>
      {(failure || attempts) && <div className="ww-channel-delivery-meta">
        {failure}{failure && attempts ? ' · ' : ''}{attempts ? '已尝试 ' + attempts + ' 次' : ''}
      </div>}
      {actions.length > 0 && !confirmAction && <div className="ww-channel-delivery-actions">
        {actions.map(row => <button type="button" key={row[0]}
          className="ww-channel-text-button" disabled={disabled || busy}
          onClick={() => setConfirmAction(row[0])}>{row[1]}</button>)}
      </div>}
      {selected && <div className="ww-channel-inline-confirm" role="alertdialog"
        aria-label="确认处理这条 Telegram 投递">
        <p>
          {selected[0] === 'resend'
            ? '此操作可能在 Telegram 中产生一条重复内容。'
            : '确认执行“' + selected[1] + '”吗？'}
        </p>
        <div className="ww-channel-inline-confirm-actions">
          <button type="button" className="ww-channel-danger-button"
            disabled={busy || disabled} onClick={resolve}>{busy ? '处理中…' : '确认处理'}</button>
          <button type="button" className="ww-channel-secondary-button"
            disabled={busy} onClick={() => setConfirmAction('')}>返回</button>
        </div>
      </div>}
      {feedback && <div className="ww-channel-feedback" role="status" aria-live="polite">
        {feedback}
      </div>}
    </div>
  );
}

function WwChannelConfirmDetails({ publicDetails, toolName }) {
  // 调用方应只传 confirm.public_details；这里仍只读取明确允许字段。
  const source = publicDetails && typeof publicDetails === 'object'
    ? publicDetails : {};
  const text = (value, limit = 256) => Array.isArray(value)
    ? value.map(item => wwChannelPublicText(item, limit)).filter(Boolean).join('、')
    : wwChannelPublicText(value, limit);
  const bot = [
    text(source.bot_display_name, 128),
    source.bot_username ? '@' + text(source.bot_username, 64).replace(/^@/, '') : '',
  ].filter(Boolean).join(' · ');
  const recipient = [
    text(source.telegram_display_name, 256),
    source.telegram_username ? '@' + text(source.telegram_username, 64).replace(/^@/, '') : '',
  ].filter(Boolean).join(' · ');
  // 发布确认必须逐字展示后端实际授权的完整说明。不能用通用展示截断器，
  // 否则用户确认的内容可能与最终发送内容不同；React 会负责文本转义。
  const exactCaption = typeof source.caption === 'string' ? source.caption : '';
  const rows = [
    ['Bot', bot],
    ['Telegram 接收者', recipient],
    ['Telegram 数字用户 ID', text(source.telegram_user_id, 64)],
    ['绑定规则', text(source.binding_policy, 320)],
    ['请求时间', text(source.requested_at, 64)],
    ['过期时间', text(source.expires_at, 64)],
    ['图片编号', text(source.media_id, 160)],
    ['发送说明', exactCaption, 'caption'],
    ['媒体范围', text(source.media_policy, 320)],
    ['任务名称', text(source.name, 128)],
    ['任务编号', text(source.automation_id, 160)],
    ['计划类型', text(source.schedule_kind, 64)],
    ['执行时间', text(source.run_at || source.local_time, 64)],
    ['时区', text(source.timezone, 64)],
    ['任务内容', text(source.prompt_summary, 600)],
    ['Skills', text(source.skill_refs, 160)],
  ].filter(row => row[1]);
  const exactRecipient = source.recipient_verified === true;
  const pairing = toolName === 'ww_telegram_pair_approve';
  const publishing = toolName === 'ww_telegram_publish';

  return (
    <section className="ww-channel-confirm-details">
      {pairing && <div className={'ww-channel-confirm-recipient ww-channel-confirm-recipient--'
        + (exactRecipient ? 'ok' : 'blocked')}>
        {exactRecipient
          ? '✓ 接收者已由完整配对码精确核验；配对码本身不会显示在公开记录中。'
          : '⚠ 接收者尚未精确核验，不能批准永久绑定。'}
      </div>}
      {publishing && <div className="ww-channel-confirm-recipient ww-channel-confirm-recipient--ok">
        本次确认仅对上述当前接收者有效。
      </div>}
      <dl className="ww-channel-confirm-list">
        {rows.map(row => <div className={'ww-channel-confirm-row'
          + (row[2] ? ' ww-channel-confirm-row--' + row[2] : '')} key={row[0]}>
          <dt>{row[0]}</dt><dd>{row[1]}</dd>
        </div>)}
      </dl>
    </section>
  );
}

function WwChannelDrawer({
  open, status, archived = false, onClose, onManage, onResolveDelivery, onResolve,
  onSubmitToken, onRefresh,
}) {
  const view = wwChannelViewState(status);
  const source = status && typeof status === 'object' ? status : {};
  const [manageBusy, setManageBusy] = React.useState('');
  const [manageConfirm, setManageConfirm] = React.useState('');
  const [feedback, setFeedback] = React.useState('');
  const bot = source.bot && typeof source.bot === 'object' ? source.bot : {};
  const recipient = source.recipient && typeof source.recipient === 'object' ? source.recipient : {};
  const counts = source.counts && typeof source.counts === 'object' ? source.counts : {};
  const deliveries = Array.isArray(source.recent_deliveries) ? source.recent_deliveries : [];
  const pairings = Array.isArray(source.pairing_requests) ? source.pairing_requests : [];
  const botUsername = wwChannelPublicText(bot.username, 64).replace(/^@/, '');
  const recipientUsername = wwChannelPublicText(recipient.username, 64).replace(/^@/, '');
  const hasChannelTarget = !!source.draft || view.connected
    || ['awaiting_token', 'awaiting_pairing', 'pairing_pending'].indexOf(view.key) >= 0;

  React.useEffect(() => {
    setManageBusy('');
    setManageConfirm('');
    setFeedback('');
  }, [open, source.session_id]);

  React.useEffect(() => {
    if (!open) return undefined;
    const onKeyDown = (event) => {
      if (event.key === 'Escape' && !manageBusy && typeof onClose === 'function') onClose();
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [open, manageBusy, onClose]);

  if (!open) return null;

  const refresh = async () => {
    if (typeof onRefresh === 'function') await onRefresh();
  };
  const manage = async (action) => {
    if (archived || manageBusy || typeof onManage !== 'function') return;
    if (action === 'disconnect' && manageConfirm !== 'disconnect') {
      setManageConfirm('disconnect');
      setFeedback('');
      return;
    }
    setManageBusy(action);
    setFeedback('');
    try {
      const result = await onManage(action);
      if (!result || result.ok !== true) {
        setFeedback(wwChannelUiSafeFailure(result, '通道操作没有完成，请重试。'));
        return;
      }
      setManageConfirm('');
      setFeedback(action === 'replace-token'
        ? '已创建新的安全提交步骤，请在 Token 卡出现后填写。'
        : '操作已提交，正在更新通道真实状态。');
      await refresh();
    } catch (_) {
      setFeedback('通道操作暂时失败，请稍后重试。');
    } finally {
      setManageBusy('');
    }
  };

  const warningCopy = {
    telegram_identity_changed: 'Bot 身份已变化，不会沿用原接收者，请重新配对。',
    credential_invalid: 'Bot 凭证已失效，请替换 Token 后重新配对。',
    credential_unavailable: 'Bot 凭证暂时不可用。',
    poll_conflict: '此 Bot 正在被另一处连接使用。',
    credential_cleanup_pending: '旧凭证正在安全清理，无需重复操作。',
    delivery_uncertain: '有投递结果待你确认。',
  };
  const warnings = [
    ...(view.statusUnavailable
      ? ['通道状态暂时不可达；已保留上次状态，恢复读取前不会执行管理操作。'] : []),
    ...(Array.isArray(source.warnings) ? source.warnings : [])
      .map(item => warningCopy[item && item.error_code]).filter(Boolean),
  ];
  const manageDisabled = archived || !view.available || view.statusUnavailable || !!manageBusy;
  const resolveDelivery = typeof onResolveDelivery === 'function'
    ? onResolveDelivery : onResolve;

  return (
    <div className="ww-channel-drawer-layer">
      <button type="button" className="ww-channel-drawer-backdrop"
        aria-label="关闭 Telegram 通道管理" onClick={onClose} />
      <aside className="ww-channel-drawer" role="dialog" aria-modal="true"
        aria-label="Telegram 通道管理">
        <header className="ww-channel-drawer-header">
          <div>
            <div className="ww-channel-eyebrow">当前对话</div>
            <h2 className="ww-channel-drawer-title">Telegram 通道</h2>
          </div>
          <button type="button" className="ww-channel-close-button"
            aria-label="关闭" onClick={onClose}>×</button>
        </header>

        <div className="ww-channel-drawer-body">
          {!view.available ? <div className="ww-channel-empty-state">
            <strong>{view.statusUnavailable ? '通道状态暂时不可达' : '通道功能当前未开启'}</strong>
            <span>{view.statusUnavailable
              ? '没有据此改变连接状态；请稍后刷新核对真实结果。'
              : '现有对话与研究功能不受影响。'}</span>
            {view.statusUnavailable && typeof onRefresh === 'function'
              && <button type="button" className="ww-channel-secondary-button"
                onClick={refresh}>重新读取状态</button>}
          </div> : <>
            <section className="ww-channel-summary-card">
              <WwChannelStatusBadge status={source} />
              {view.botName && <div className="ww-channel-identity-line">
                <span>Bot</span><strong>{view.botName}</strong>
                {botUsername && wwChannelPublicText(bot.display_name, 128)
                  && <small>@{botUsername}</small>}
              </div>}
              {recipient.user_id && <div className="ww-channel-identity-line">
                <span>接收者</span>
                <strong>{wwChannelPublicText(recipient.display_name, 256)
                  || (recipientUsername ? '@' + recipientUsername : 'Telegram 用户')}</strong>
                <small>{recipientUsername ? '@' + recipientUsername + ' · ' : ''}
                  ID {wwChannelPublicText(recipient.user_id, 64)}</small>
              </div>}
              {archived && <p className="ww-channel-readonly-note">
                已归档对话仅供查看。请先恢复对话，再显式恢复或管理通道。
              </p>}
            </section>

            {warnings.length > 0 && <section className="ww-channel-warning-card" role="status">
              {warnings.map((message, index) => <p key={index}>{message}</p>)}
            </section>}

            {view.key === 'awaiting_token' && <WwSecureTokenCard status={source}
              disabled={archived || view.statusUnavailable}
              onSubmit={onSubmitToken} onSuccess={refresh} />}

            {view.key === 'awaiting_pairing' && <section className="ww-channel-guide-card">
              <h3>下一步：私聊 Bot</h3>
              <p>在 Telegram 中打开该 Bot 并发送任意私聊消息。Bot 会返回一小时内有效的配对码。</p>
            </section>}

            {pairings.length > 0 && <section className="ww-channel-section">
              <div className="ww-channel-section-head">
                <h3>待核对接收者</h3><span>{pairings.length} 个</span>
              </div>
              {pairings.map(item => <div className="ww-channel-pairing-row"
                key={wwChannelPublicText(item.request_id, 160)}>
                <strong>{wwChannelPublicText(item.display_name, 256)
                  || (item.username ? '@' + wwChannelPublicText(item.username, 64).replace(/^@/, '') : 'Telegram 用户')}</strong>
                <span>{item.username ? '@' + wwChannelPublicText(item.username, 64).replace(/^@/, '') + ' · ' : ''}
                  ID {wwChannelPublicText(item.user_id, 64)}</span>
                <small>请将完整配对码交给 Agent 后核对</small>
              </div>)}
            </section>}

            <section className="ww-channel-section">
              <div className="ww-channel-section-head"><h3>投递概览</h3></div>
              <div className="ww-channel-count-grid">
                <div><span>待发送</span><strong>{Math.max(0, Number(counts.pending) || 0)}</strong></div>
                <div><span>重试中</span><strong>{Math.max(0, Number(counts.retry_wait) || 0)}</strong></div>
                <div><span>待确认</span><strong>{Math.max(0, Number(counts.uncertain) || 0)}</strong></div>
                <div><span>已停止</span><strong>{Math.max(0, Number(counts.terminal) || 0)}</strong></div>
              </div>
            </section>

            <section className="ww-channel-section">
              <div className="ww-channel-section-head"><h3>最近投递</h3><span>{deliveries.length} 条</span></div>
              {deliveries.length > 0 ? deliveries.map(item => <WwDeliveryRow
                key={wwChannelPublicText(item.outbox_id, 160)} delivery={item}
                disabled={archived || view.statusUnavailable}
                onResolve={resolveDelivery} onRefresh={refresh} />)
                : <p className="ww-channel-empty-copy">还没有投递记录。</p>}
            </section>

            <section className="ww-channel-section">
              <div className="ww-channel-section-head"><h3>连接管理</h3></div>
              <div className="ww-channel-manage-grid">
                {view.key === 'paused'
                  ? <button type="button" className="ww-channel-secondary-button"
                    disabled={manageDisabled} onClick={() => manage('resume')}>
                    {manageBusy === 'resume' ? '恢复中…' : '恢复镜像'}
                  </button>
                  : <button type="button" className="ww-channel-secondary-button"
                    disabled={manageDisabled || !view.connected}
                    onClick={() => manage('pause')}>
                    {manageBusy === 'pause' ? '暂停中…' : '暂停镜像'}
                  </button>}
                <button type="button" className="ww-channel-secondary-button"
                  disabled={manageDisabled || !view.connected} onClick={() => manage('reverify')}>
                  {manageBusy === 'reverify' ? '校验中…' : '重新校验 Bot'}
                </button>
                <button type="button" className="ww-channel-secondary-button"
                  disabled={manageDisabled || (!view.connected && view.key !== 'awaiting_pairing')}
                  onClick={() => manage('replace-token')}>
                  {manageBusy === 'replace-token' ? '准备中…' : '替换 Token'}
                </button>
                <button type="button" className="ww-channel-danger-link"
                  disabled={manageDisabled || !hasChannelTarget} onClick={() => manage('disconnect')}>
                  {view.connected ? '解除连接' : '取消连接设置'}
                </button>
              </div>
              <p className="ww-channel-manage-note">
                替换凭证只会打开新的安全提交卡。如果 Bot 身份改变，必须重新配对。
              </p>
            </section>

            {manageConfirm === 'disconnect' && <section className="ww-channel-disconnect-confirm"
              role="alertdialog" aria-label="确认解除 Telegram 连接">
              <h3>{view.connected ? '确认解除连接？' : '确认取消连接设置？'}</h3>
              <p>{view.connected
                ? '尚未发送的内容会取消，并停止新镜像；帷幄对话历史不会删除。以后重新连接也不会补发旧消息。'
                : '当前连接草稿与配对等待会取消；帷幄对话历史不会删除。'}</p>
              <div className="ww-channel-inline-confirm-actions">
                <button type="button" className="ww-channel-danger-button"
                  disabled={!!manageBusy} onClick={() => manage('disconnect')}>
                  {manageBusy === 'disconnect' ? '解除中…' : '确认解除'}
                </button>
                <button type="button" className="ww-channel-secondary-button"
                  disabled={!!manageBusy} onClick={() => setManageConfirm('')}>返回</button>
              </div>
            </section>}

            {feedback && <div className="ww-channel-feedback" role="status" aria-live="polite">
              {feedback}
            </div>}
          </>}
        </div>
      </aside>
    </div>
  );
}

window.WwChannelStatusBadge = WwChannelStatusBadge;
window.WwChannelActionCard = WwChannelActionCard;
window.WwSecureTokenCard = WwSecureTokenCard;
window.WwChannelDrawer = WwChannelDrawer;
window.WwDeliveryRow = WwDeliveryRow;
window.WwChannelConfirmDetails = WwChannelConfirmDetails;
window.wwChannelViewState = wwChannelViewState;
