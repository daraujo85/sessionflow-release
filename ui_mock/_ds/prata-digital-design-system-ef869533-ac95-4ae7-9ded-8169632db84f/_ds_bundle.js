/* @ds-bundle: {"format":3,"namespace":"PrataDigitalDesignSystem_ef8695","components":[{"name":"Button","sourcePath":"components/buttons/Button.jsx"},{"name":"StatusTag","sourcePath":"components/buttons/StatusTag.jsx"},{"name":"Tag","sourcePath":"components/buttons/Tag.jsx"},{"name":"Table","sourcePath":"components/data/Table.jsx"},{"name":"Modal","sourcePath":"components/feedback/Modal.jsx"},{"name":"Checkbox","sourcePath":"components/forms/Checkbox.jsx"},{"name":"Input","sourcePath":"components/forms/Input.jsx"},{"name":"Select","sourcePath":"components/forms/Select.jsx"},{"name":"Switch","sourcePath":"components/forms/Switch.jsx"},{"name":"Tabs","sourcePath":"components/navigation/Tabs.jsx"},{"name":"Card","sourcePath":"components/surfaces/Card.jsx"},{"name":"StatCard","sourcePath":"components/surfaces/StatCard.jsx"}],"sourceHashes":{"components/buttons/Button.jsx":"bd00b5ac1c7f","components/buttons/StatusTag.jsx":"679dbdde0d3c","components/buttons/Tag.jsx":"c52386ffbd56","components/data/Table.jsx":"3b94fc8f4917","components/feedback/Modal.jsx":"70b4a9030153","components/forms/Checkbox.jsx":"510cf4301f7b","components/forms/Input.jsx":"6755887d3b78","components/forms/Select.jsx":"852d432ff607","components/forms/Switch.jsx":"656a56b45dd8","components/navigation/Tabs.jsx":"31eccd4f8675","components/surfaces/Card.jsx":"bae54da0977f","components/surfaces/StatCard.jsx":"f648c1dd153f"},"inlinedExternals":[],"unexposedExports":[]} */

(() => {

const __ds_ns = (window.PrataDigitalDesignSystem_ef8695 = window.PrataDigitalDesignSystem_ef8695 || {});

const __ds_scope = {};

(__ds_ns.__errors = __ds_ns.__errors || []);

// components/buttons/Button.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
/**
 * Prata Digital — Button
 * Mirrors the admin `button.button` variants: primary action green,
 * outlined, ghost, link (indigo) and danger.
 */
function Button({
  children,
  variant = 'primary',
  size = 'md',
  disabled = false,
  loading = false,
  fullWidth = false,
  iconLeft = null,
  iconRight = null,
  type = 'button',
  onClick,
  style = {},
  ...rest
}) {
  const sizes = {
    sm: {
      padding: '8px 12px',
      fontSize: 13
    },
    md: {
      padding: '12px 16px',
      fontSize: 14
    },
    lg: {
      padding: '14px 20px',
      fontSize: 15
    }
  };
  const base = {
    display: 'inline-flex',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 8,
    fontFamily: 'var(--font-sans)',
    fontWeight: 500,
    lineHeight: '20px',
    borderRadius: 'var(--radius-md)',
    border: '1px solid transparent',
    cursor: disabled || loading ? 'not-allowed' : 'pointer',
    opacity: loading ? 0.6 : 1,
    width: fullWidth ? '100%' : 'auto',
    transition: 'background-color var(--dur-fast) var(--ease-standard), opacity var(--dur-fast)',
    textDecoration: 'none',
    whiteSpace: 'nowrap',
    ...sizes[size]
  };
  const variants = {
    primary: {
      background: 'var(--action-primary)',
      color: '#fff'
    },
    outline: {
      background: '#fff',
      color: 'var(--stone-700)',
      borderColor: 'var(--stone-300)'
    },
    ghost: {
      background: 'transparent',
      color: 'var(--stone-500)'
    },
    link: {
      background: 'transparent',
      color: 'var(--text-link)',
      textDecoration: 'underline',
      padding: 0
    },
    danger: {
      background: 'var(--red-100)',
      color: 'var(--red-700)'
    }
  };
  const disabledStyle = disabled && !loading ? {
    opacity: 0.5
  } : {};
  return /*#__PURE__*/React.createElement("button", _extends({
    type: type,
    disabled: disabled || loading,
    onClick: onClick,
    style: {
      ...base,
      ...variants[variant],
      ...disabledStyle,
      ...style
    },
    onMouseEnter: e => {
      if (disabled || loading) return;
      if (variant === 'primary') e.currentTarget.style.background = 'var(--action-primary-hover)';else if (variant === 'outline') e.currentTarget.style.background = 'var(--stone-50)';else if (variant === 'ghost') e.currentTarget.style.background = 'var(--stone-100)';else if (variant === 'danger') e.currentTarget.style.background = 'var(--red-200)';
    },
    onMouseLeave: e => {
      e.currentTarget.style.background = variants[variant].background;
    }
  }, rest), loading ? /*#__PURE__*/React.createElement(Spinner, null) : /*#__PURE__*/React.createElement(React.Fragment, null, iconLeft, children, iconRight));
}
function Spinner() {
  return /*#__PURE__*/React.createElement("span", {
    style: {
      width: 16,
      height: 16,
      border: '2px solid rgba(255,255,255,0.5)',
      borderTopColor: '#fff',
      borderRadius: '50%',
      display: 'inline-block',
      animation: 'pd-spin 0.7s linear infinite'
    }
  });
}
Object.assign(__ds_scope, { Button });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/buttons/Button.jsx", error: String((e && e.message) || e) }); }

// components/buttons/Tag.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
/**
 * Prata Digital — Tag
 * The signature pastel pill. bg/text pairs match the .tag colour system.
 */
function Tag({
  children,
  tone = 'neutral',
  style = {},
  ...rest
}) {
  const tones = {
    success: {
      bg: 'var(--success-bg)',
      fg: 'var(--success-fg)'
    },
    info: {
      bg: 'var(--info-bg)',
      fg: 'var(--info-fg)'
    },
    warning: {
      bg: 'var(--warning-bg)',
      fg: 'var(--warning-fg)'
    },
    danger: {
      bg: 'var(--danger-bg)',
      fg: 'var(--danger-fg)'
    },
    hold: {
      bg: 'var(--hold-bg)',
      fg: 'var(--hold-fg)'
    },
    neutral: {
      bg: 'var(--neutral-bg)',
      fg: 'var(--neutral-fg)'
    }
  };
  const t = tones[tone] || tones.neutral;
  return /*#__PURE__*/React.createElement("span", _extends({
    style: {
      display: 'inline-flex',
      alignItems: 'center',
      gap: 6,
      background: t.bg,
      color: t.fg,
      fontFamily: 'var(--font-sans)',
      fontWeight: 500,
      fontSize: 12,
      lineHeight: '16px',
      padding: '2px 10px',
      borderRadius: 'var(--radius-xl)',
      whiteSpace: 'nowrap',
      ...style
    }
  }, rest), children);
}
Object.assign(__ds_scope, { Tag });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/buttons/Tag.jsx", error: String((e && e.message) || e) }); }

// components/buttons/StatusTag.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
/**
 * Prata Digital — StatusTag
 * A Tag with a leading status dot. Maps common proposal statuses
 * (pt-BR) to the right tone, or pass `tone` explicitly.
 */
const STATUS_TONES = {
  aprovada: 'success',
  aprovado: 'success',
  pago: 'success',
  ativo: 'success',
  'em análise': 'info',
  'em analise': 'info',
  analise: 'info',
  pendente: 'warning',
  aguardando: 'warning',
  reprovada: 'danger',
  reprovado: 'danger',
  cancelada: 'danger',
  erro: 'danger',
  'em espera': 'hold',
  rascunho: 'neutral'
};
function StatusTag({
  children,
  status,
  tone,
  style = {},
  ...rest
}) {
  const label = children ?? status ?? '';
  const key = String(label).trim().toLowerCase();
  const resolved = tone || STATUS_TONES[key] || 'neutral';
  return /*#__PURE__*/React.createElement(__ds_scope.Tag, _extends({
    tone: resolved,
    style: style
  }, rest), /*#__PURE__*/React.createElement("span", {
    style: {
      width: 7,
      height: 7,
      borderRadius: '50%',
      background: 'currentColor',
      display: 'inline-block',
      flex: 'none'
    }
  }), label);
}
Object.assign(__ds_scope, { StatusTag });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/buttons/StatusTag.jsx", error: String((e && e.message) || e) }); }

// components/data/Table.jsx
try { (() => {
/**
 * Prata Digital — Table
 * Clean data table. Columns define key/label/align/render; rows are objects.
 * Header on a stone tint, hairline row separators, mint row hover.
 */
function Table({
  columns = [],
  rows = [],
  rowKey,
  emptyText = 'Nenhum registro encontrado',
  style = {}
}) {
  const [hover, setHover] = React.useState(-1);
  const alignOf = a => a === 'right' ? 'right' : a === 'center' ? 'center' : 'left';
  return /*#__PURE__*/React.createElement("div", {
    style: {
      width: '100%',
      overflowX: 'auto',
      ...style
    }
  }, /*#__PURE__*/React.createElement("table", {
    style: {
      width: '100%',
      borderCollapse: 'collapse',
      fontFamily: 'var(--font-sans)'
    }
  }, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, columns.map(c => /*#__PURE__*/React.createElement("th", {
    key: c.key,
    style: {
      textAlign: alignOf(c.align),
      padding: '12px 16px',
      font: '600 12px/16px var(--font-sans)',
      color: 'var(--text-muted)',
      background: 'var(--stone-50)',
      borderBottom: '1px solid var(--border-subtle)',
      whiteSpace: 'nowrap',
      textTransform: 'uppercase',
      letterSpacing: '0.03em'
    }
  }, c.label)))), /*#__PURE__*/React.createElement("tbody", null, rows.length === 0 && /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("td", {
    colSpan: columns.length,
    style: {
      padding: '28px 16px',
      textAlign: 'center',
      color: 'var(--text-muted)',
      font: '400 14px/20px var(--font-sans)'
    }
  }, emptyText)), rows.map((row, i) => /*#__PURE__*/React.createElement("tr", {
    key: rowKey ? row[rowKey] : i,
    onMouseEnter: () => setHover(i),
    onMouseLeave: () => setHover(-1),
    style: {
      background: hover === i ? 'var(--prata-green-50)' : 'transparent',
      transition: 'background var(--dur-fast)'
    }
  }, columns.map(c => /*#__PURE__*/React.createElement("td", {
    key: c.key,
    style: {
      textAlign: alignOf(c.align),
      padding: '14px 16px',
      font: '400 14px/20px var(--font-sans)',
      color: 'var(--text-body)',
      borderBottom: '1px solid var(--border-subtle)',
      whiteSpace: 'nowrap'
    }
  }, c.render ? c.render(row[c.key], row, i) : row[c.key])))))));
}
Object.assign(__ds_scope, { Table });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/data/Table.jsx", error: String((e && e.message) || e) }); }

// components/feedback/Modal.jsx
try { (() => {
/**
 * Prata Digital — Modal
 * Centered dialog over a dimmed overlay. Title, close, body and optional footer.
 */
function Modal({
  open = true,
  onClose,
  title,
  children,
  footer,
  width = 480,
  style = {}
}) {
  if (!open) return null;
  return /*#__PURE__*/React.createElement("div", {
    onClick: onClose,
    style: {
      position: 'fixed',
      inset: 0,
      background: 'rgba(12, 10, 9, 0.45)',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      padding: 24,
      zIndex: 1000
    }
  }, /*#__PURE__*/React.createElement("div", {
    onClick: e => e.stopPropagation(),
    style: {
      background: '#fff',
      borderRadius: 'var(--radius-2xl)',
      boxShadow: 'var(--shadow-lg)',
      width: '100%',
      maxWidth: width,
      maxHeight: '90vh',
      overflow: 'auto',
      ...style
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'space-between',
      padding: '20px 24px',
      borderBottom: '1px solid var(--border-subtle)'
    }
  }, /*#__PURE__*/React.createElement("h3", {
    style: {
      font: '700 18px/24px var(--font-sans)',
      color: 'var(--text-strong)',
      fontFamily: 'var(--font-sans)',
      letterSpacing: 0
    }
  }, title), onClose && /*#__PURE__*/React.createElement("button", {
    onClick: onClose,
    "aria-label": "Fechar",
    style: {
      border: 'none',
      background: 'transparent',
      cursor: 'pointer',
      color: 'var(--gray-500)',
      fontSize: 20,
      lineHeight: 1,
      padding: 4
    }
  }, "\u2715")), /*#__PURE__*/React.createElement("div", {
    style: {
      padding: 24,
      font: '400 14px/20px var(--font-sans)',
      color: 'var(--text-body)'
    }
  }, children), footer && /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      justifyContent: 'flex-end',
      gap: 12,
      padding: '16px 24px',
      borderTop: '1px solid var(--border-subtle)'
    }
  }, footer)));
}
Object.assign(__ds_scope, { Modal });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/feedback/Modal.jsx", error: String((e && e.message) || e) }); }

// components/forms/Checkbox.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
/**
 * Prata Digital — Checkbox
 * Square check, action-green when selected.
 */
function Checkbox({
  checked = false,
  onChange,
  label,
  disabled = false,
  style = {},
  ...rest
}) {
  return /*#__PURE__*/React.createElement("label", {
    style: {
      display: 'inline-flex',
      alignItems: 'center',
      gap: 8,
      cursor: disabled ? 'not-allowed' : 'pointer',
      opacity: disabled ? 0.5 : 1,
      font: '400 14px/20px var(--font-sans)',
      color: 'var(--text-body)',
      ...style
    }
  }, /*#__PURE__*/React.createElement("span", {
    onClick: () => !disabled && onChange && onChange(!checked),
    style: {
      width: 18,
      height: 18,
      flex: 'none',
      borderRadius: 'var(--radius-sm)',
      border: `1.5px solid ${checked ? 'var(--action-primary)' : 'var(--gray-300)'}`,
      background: checked ? 'var(--action-primary)' : '#fff',
      display: 'inline-flex',
      alignItems: 'center',
      justifyContent: 'center',
      transition: 'all var(--dur-fast)'
    }
  }, checked && /*#__PURE__*/React.createElement("svg", {
    width: "11",
    height: "9",
    viewBox: "0 0 11 9",
    fill: "none"
  }, /*#__PURE__*/React.createElement("path", {
    d: "M1 4.5L4 7.5L10 1.5",
    stroke: "#fff",
    strokeWidth: "2",
    strokeLinecap: "round",
    strokeLinejoin: "round"
  }))), label, /*#__PURE__*/React.createElement("input", _extends({
    type: "checkbox",
    checked: checked,
    disabled: disabled,
    onChange: () => {},
    style: {
      display: 'none'
    }
  }, rest)));
}
Object.assign(__ds_scope, { Checkbox });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/forms/Checkbox.jsx", error: String((e && e.message) || e) }); }

// components/forms/Input.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
/**
 * Prata Digital — Input
 * Label above, 42px field, 6px radius, gray-300 border. Optional error + hint.
 */
function Input({
  label,
  value,
  onChange,
  placeholder = '',
  type = 'text',
  error = '',
  hint = '',
  disabled = false,
  required = false,
  iconRight = null,
  style = {},
  ...rest
}) {
  const [focused, setFocused] = React.useState(false);
  return /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      flexDirection: 'column',
      gap: 4,
      ...style
    }
  }, label && /*#__PURE__*/React.createElement("label", {
    style: {
      font: '400 14px/20px var(--font-sans)',
      color: '#666',
      display: 'flex',
      gap: 4
    }
  }, label, required && /*#__PURE__*/React.createElement("span", {
    style: {
      color: 'var(--red-600)'
    }
  }, "*")), /*#__PURE__*/React.createElement("div", {
    style: {
      position: 'relative',
      display: 'flex',
      alignItems: 'center'
    }
  }, /*#__PURE__*/React.createElement("input", _extends({
    type: type,
    value: value,
    placeholder: placeholder,
    disabled: disabled,
    onChange: onChange,
    onFocus: () => setFocused(true),
    onBlur: () => setFocused(false),
    style: {
      width: '100%',
      height: 42,
      padding: iconRight ? '0 38px 0 12px' : '0 12px',
      fontFamily: 'var(--font-sans)',
      fontSize: 16,
      color: 'var(--text-body)',
      background: disabled ? 'var(--gray-100)' : '#fff',
      border: `1px solid ${error ? 'var(--red-600)' : focused ? 'var(--action-primary)' : 'var(--gray-300)'}`,
      borderRadius: 'var(--radius-md)',
      outline: 'none',
      boxShadow: focused && !error ? '0 0 0 3px var(--focus-ring)' : 'none',
      transition: 'border-color var(--dur-fast), box-shadow var(--dur-fast)',
      boxSizing: 'border-box'
    }
  }, rest)), iconRight && /*#__PURE__*/React.createElement("span", {
    style: {
      position: 'absolute',
      right: 12,
      display: 'inline-flex',
      pointerEvents: 'none'
    }
  }, iconRight)), error ? /*#__PURE__*/React.createElement("small", {
    style: {
      font: '400 12px/16px var(--font-sans)',
      color: 'var(--red-600)'
    }
  }, error) : hint ? /*#__PURE__*/React.createElement("small", {
    style: {
      font: '400 12px/16px var(--font-sans)',
      color: 'var(--text-muted)'
    }
  }, hint) : null);
}
Object.assign(__ds_scope, { Input });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/forms/Input.jsx", error: String((e && e.message) || e) }); }

// components/forms/Select.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
/**
 * Prata Digital — Select
 * Native select styled to match Input (42px, 6px radius, gray-300 border).
 */
function Select({
  label,
  value,
  onChange,
  options = [],
  placeholder = 'Selecione',
  error = '',
  hint = '',
  disabled = false,
  required = false,
  style = {},
  ...rest
}) {
  const [focused, setFocused] = React.useState(false);
  const opts = options.map(o => typeof o === 'string' ? {
    value: o,
    label: o
  } : o);
  return /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      flexDirection: 'column',
      gap: 4,
      ...style
    }
  }, label && /*#__PURE__*/React.createElement("label", {
    style: {
      font: '400 14px/20px var(--font-sans)',
      color: '#666',
      display: 'flex',
      gap: 4
    }
  }, label, required && /*#__PURE__*/React.createElement("span", {
    style: {
      color: 'var(--red-600)'
    }
  }, "*")), /*#__PURE__*/React.createElement("div", {
    style: {
      position: 'relative',
      display: 'flex',
      alignItems: 'center'
    }
  }, /*#__PURE__*/React.createElement("select", _extends({
    value: value,
    disabled: disabled,
    onChange: onChange,
    onFocus: () => setFocused(true),
    onBlur: () => setFocused(false),
    style: {
      width: '100%',
      height: 42,
      padding: '0 36px 0 12px',
      fontFamily: 'var(--font-sans)',
      fontSize: 16,
      color: value ? 'var(--text-body)' : 'var(--gray-500)',
      background: disabled ? 'var(--gray-100)' : '#fff',
      border: `1px solid ${error ? 'var(--red-600)' : focused ? 'var(--action-primary)' : 'var(--gray-300)'}`,
      borderRadius: 'var(--radius-md)',
      outline: 'none',
      boxShadow: focused && !error ? '0 0 0 3px var(--focus-ring)' : 'none',
      appearance: 'none',
      WebkitAppearance: 'none',
      cursor: disabled ? 'not-allowed' : 'pointer',
      boxSizing: 'border-box'
    }
  }, rest), placeholder && /*#__PURE__*/React.createElement("option", {
    value: ""
  }, placeholder), opts.map(o => /*#__PURE__*/React.createElement("option", {
    key: o.value,
    value: o.value
  }, o.label))), /*#__PURE__*/React.createElement("span", {
    style: {
      position: 'absolute',
      right: 12,
      pointerEvents: 'none',
      borderLeft: '5px solid transparent',
      borderRight: '5px solid transparent',
      borderTop: '6px solid var(--gray-500)'
    }
  })), error ? /*#__PURE__*/React.createElement("small", {
    style: {
      font: '400 12px/16px var(--font-sans)',
      color: 'var(--red-600)'
    }
  }, error) : hint ? /*#__PURE__*/React.createElement("small", {
    style: {
      font: '400 12px/16px var(--font-sans)',
      color: 'var(--text-muted)'
    }
  }, hint) : null);
}
Object.assign(__ds_scope, { Select });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/forms/Select.jsx", error: String((e && e.message) || e) }); }

// components/forms/Switch.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
/**
 * Prata Digital — Switch
 * Rounded toggle, action-green when on (Bulma switch is-rounded).
 */
function Switch({
  checked = false,
  onChange,
  label,
  disabled = false,
  style = {},
  ...rest
}) {
  return /*#__PURE__*/React.createElement("label", {
    style: {
      display: 'inline-flex',
      alignItems: 'center',
      gap: 10,
      cursor: disabled ? 'not-allowed' : 'pointer',
      opacity: disabled ? 0.5 : 1,
      font: '400 14px/20px var(--font-sans)',
      color: 'var(--text-body)',
      ...style
    }
  }, /*#__PURE__*/React.createElement("span", {
    onClick: () => !disabled && onChange && onChange(!checked),
    style: {
      width: 40,
      height: 22,
      flex: 'none',
      borderRadius: 'var(--radius-full)',
      background: checked ? 'var(--action-primary)' : 'var(--gray-300)',
      position: 'relative',
      transition: 'background var(--dur-normal)'
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      position: 'absolute',
      top: 2,
      left: checked ? 20 : 2,
      width: 18,
      height: 18,
      borderRadius: '50%',
      background: '#fff',
      boxShadow: '0 1px 2px rgba(0,0,0,0.2)',
      transition: 'left var(--dur-normal) var(--ease-standard)'
    }
  })), label, /*#__PURE__*/React.createElement("input", _extends({
    type: "checkbox",
    checked: checked,
    disabled: disabled,
    onChange: () => {},
    style: {
      display: 'none'
    }
  }, rest)));
}
Object.assign(__ds_scope, { Switch });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/forms/Switch.jsx", error: String((e && e.message) || e) }); }

// components/navigation/Tabs.jsx
try { (() => {
/**
 * Prata Digital — Tabs
 * Header strip with numbered/iconed prefix chips and an active underline.
 * Controlled: pass `value` (active name) and `onChange`.
 */
function Tabs({
  items = [],
  value,
  onChange,
  showCounter = true,
  style = {}
}) {
  return /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      gap: 2,
      borderBottom: '1px solid var(--border-subtle)',
      ...style
    }
  }, items.map((tab, i) => {
    const active = value === tab.name;
    return /*#__PURE__*/React.createElement("div", {
      key: tab.name,
      onClick: () => onChange && onChange(tab.name),
      style: {
        position: 'relative',
        display: 'flex',
        alignItems: 'center',
        gap: 8,
        padding: '0 12px 10px',
        cursor: 'pointer',
        flex: 1,
        justifyContent: 'center'
      }
    }, showCounter && /*#__PURE__*/React.createElement("span", {
      style: {
        display: 'grid',
        placeItems: 'center',
        minWidth: 26,
        padding: '4px 8px',
        borderRadius: 'var(--radius-xl)',
        font: '600 12px/1 var(--font-sans)',
        background: active ? 'var(--prata-green-100)' : 'var(--gray-100)',
        color: active ? 'var(--action-primary)' : 'var(--gray-500)',
        transition: 'all var(--dur-normal)'
      }
    }, tab.icon || i + 1), /*#__PURE__*/React.createElement("span", {
      style: {
        font: `${active ? 700 : 500} 14px/20px var(--font-sans)`,
        color: active ? 'var(--action-primary)' : 'var(--gray-400)',
        whiteSpace: 'nowrap',
        transition: 'color var(--dur-normal)'
      }
    }, tab.label), active && /*#__PURE__*/React.createElement("span", {
      style: {
        position: 'absolute',
        bottom: -1,
        left: 0,
        width: '100%',
        height: 3,
        background: 'var(--prata-green-500)',
        borderRadius: '3px 3px 0 0'
      }
    }));
  }));
}
Object.assign(__ds_scope, { Tabs });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/navigation/Tabs.jsx", error: String((e && e.message) || e) }); }

// components/surfaces/Card.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
/**
 * Prata Digital — Card
 * The `.is-card`: white, 8px radius, stone-100 border, soft shadow.
 */
function Card({
  children,
  title,
  action,
  padded = true,
  style = {},
  ...rest
}) {
  return /*#__PURE__*/React.createElement("div", _extends({
    style: {
      background: 'var(--surface-card)',
      border: '1px solid var(--border-subtle)',
      borderRadius: 'var(--radius-lg)',
      boxShadow: 'var(--shadow-xs)',
      ...style
    }
  }, rest), (title || action) && /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'space-between',
      padding: '16px 16px 0'
    }
  }, title && /*#__PURE__*/React.createElement("h3", {
    style: {
      font: '600 16px/24px var(--font-sans)',
      color: 'var(--text-strong)',
      fontFamily: 'var(--font-sans)',
      letterSpacing: 0
    }
  }, title), action), /*#__PURE__*/React.createElement("div", {
    style: {
      padding: padded ? '16px' : 0
    }
  }, children));
}
Object.assign(__ds_scope, { Card });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/surfaces/Card.jsx", error: String((e && e.message) || e) }); }

// components/surfaces/StatCard.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
/**
 * Prata Digital — StatCard
 * KPI tile: label, big value, optional delta. 10px radius, lifts on hover
 * (value + label turn mint), mirroring the admin dashboard BaseCard.
 */
function StatCard({
  label,
  value,
  delta,
  deltaTone = 'up',
  icon = null,
  style = {},
  ...rest
}) {
  const [hover, setHover] = React.useState(false);
  const deltaColor = deltaTone === 'down' ? 'var(--red-600)' : 'var(--green-600)';
  return /*#__PURE__*/React.createElement("div", _extends({
    onMouseEnter: () => setHover(true),
    onMouseLeave: () => setHover(false),
    style: {
      background: hover ? 'var(--color-light)' : 'var(--surface-card)',
      border: '1px solid var(--border-subtle)',
      borderRadius: 'var(--radius-xl)',
      boxShadow: hover ? 'var(--shadow-hover)' : 'var(--shadow-sm)',
      padding: 20,
      transition: 'box-shadow var(--dur-slow) var(--ease-standard), background var(--dur-slow)',
      cursor: 'default',
      ...style
    }
  }, rest), /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      justifyContent: 'space-between',
      alignItems: 'flex-start'
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      font: '500 13px/16px var(--font-sans)',
      color: hover ? 'var(--action-primary)' : 'var(--text-muted)',
      transition: 'color var(--dur-slow)'
    }
  }, label), icon && /*#__PURE__*/React.createElement("span", {
    style: {
      opacity: 0.7
    }
  }, icon)), /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      alignItems: 'baseline',
      gap: 10,
      marginTop: 10
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      font: '800 30px/1 var(--font-sans)',
      color: hover ? 'var(--action-primary)' : 'var(--text-strong)',
      transition: 'color var(--dur-slow)'
    }
  }, value), delta != null && /*#__PURE__*/React.createElement("span", {
    style: {
      font: '600 13px/1 var(--font-sans)',
      color: deltaColor
    }
  }, deltaTone === 'down' ? '▾' : '▴', " ", delta)));
}
Object.assign(__ds_scope, { StatCard });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/surfaces/StatCard.jsx", error: String((e && e.message) || e) }); }

__ds_ns.Button = __ds_scope.Button;

__ds_ns.StatusTag = __ds_scope.StatusTag;

__ds_ns.Tag = __ds_scope.Tag;

__ds_ns.Table = __ds_scope.Table;

__ds_ns.Modal = __ds_scope.Modal;

__ds_ns.Checkbox = __ds_scope.Checkbox;

__ds_ns.Input = __ds_scope.Input;

__ds_ns.Select = __ds_scope.Select;

__ds_ns.Switch = __ds_scope.Switch;

__ds_ns.Tabs = __ds_scope.Tabs;

__ds_ns.Card = __ds_scope.Card;

__ds_ns.StatCard = __ds_scope.StatCard;

})();
