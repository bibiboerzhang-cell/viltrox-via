module.exports = {
  content: ['./src/**/*.{ts,tsx,js,jsx}', './index.html'],
  // data-theme="dark" 驱动 dark: 变体(3.4+ selector 策略)。颜色主要靠 --ds-* 变量,
  // dark: 前缀基本用不上,仅留给个别需要显式分叉的场景。
  darkMode: ['selector', '[data-theme="dark"]'],
  corePlugins: {
    preflight: false, // 沿用现状,勿开(会污染既有全局样式)
  },
  theme: {
    extend: {
      colors: {
        // 直接吃成品色 token(不带 <alpha-value>);需要透明用 *-soft
        bg: 'var(--ds-bg)',
        panel: 'var(--ds-panel)',
        card: 'var(--ds-card)',
        line: 'var(--ds-line)',
        'line-strong': 'var(--ds-line-strong)',
        ink: 'var(--ds-text)',
        'ink-2': 'var(--ds-text-2)',
        muted: 'var(--ds-muted)',
        accent: 'var(--ds-accent)',
        'accent-hover': 'var(--ds-accent-hover)',
        'accent-soft': 'var(--ds-accent-soft)',
        'accent-2': 'var(--ds-accent-2)',
        good: 'var(--ds-good)',
        'good-soft': 'var(--ds-good-soft)',
        warn: 'var(--ds-warn)',
        'warn-soft': 'var(--ds-warn-soft)',
        crit: 'var(--ds-crit)',
        'crit-soft': 'var(--ds-crit-soft)',
        info: 'var(--ds-info)',
        'info-soft': 'var(--ds-info-soft)',
      },
      borderRadius: {
        // ds- 前缀:不覆盖 Tailwind 默认 rounded-*,避免动到既有组件;
        // 组件按波次改用 rounded-ds-lg 等主动接入 token 化圆角。
        'ds-sm': 'var(--ds-radius-sm)',
        ds: 'var(--ds-radius-md)',
        'ds-md': 'var(--ds-radius-md)',
        'ds-lg': 'var(--ds-radius-lg)',
        'ds-xl': 'var(--ds-radius-xl)',
      },
      boxShadow: {
        ds: 'var(--ds-shadow)',
        'ds-sm': 'var(--ds-shadow-sm)',
      },
      fontFamily: {
        sans: ['var(--ds-font-sans)'],
        mono: ['var(--ds-font-mono)'],
      },
    },
  },
  plugins: [],
};
