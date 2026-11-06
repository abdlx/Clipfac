/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        // Lumen · Night Foundry — values mirror tokens.css (kept literal so
        // Tailwind alpha modifiers like bg-brass/10 compile)
        paper: "rgb(var(--cf-paper) / <alpha-value>)",
        paper2: "rgb(var(--cf-panel) / <alpha-value>)",
        paper3: "rgb(var(--cf-raised) / <alpha-value>)",
        ink: "rgb(var(--cf-ink) / <alpha-value>)",
        ink2: "rgb(var(--cf-ink2) / <alpha-value>)",
        muted: "rgb(var(--cf-muted) / <alpha-value>)",
        brass: "rgb(var(--cf-accent) / <alpha-value>)",
        brassink: "rgb(var(--cf-accent-ink) / <alpha-value>)",
        coral: "#0D6CE5",
        ok: "#0B7A52",
        warn: "#9A6400",
        danger: "#B42318",
        // legacy aliases so untouched files degrade gracefully
        background: "rgb(var(--cf-paper) / <alpha-value>)",
        surface: "rgb(var(--cf-panel) / <alpha-value>)",
        primary: "rgb(var(--cf-accent) / <alpha-value>)",
        accent: "rgb(var(--cf-accent) / <alpha-value>)",
      },
      fontFamily: {
        display: "var(--font-display)",
        body: "var(--font-body)",
        sans: "var(--font-body)",
        serif: "var(--font-display)",
        mono: "var(--font-mono)",
      },
      borderColor: {
        rule: "var(--color-rule)",
        rule2: "var(--color-rule-2)",
      },
      borderRadius: {
        card: "var(--radius-card)",
        input: "var(--radius-input)",
      },
      fontSize: {
        micro: ["10.5px", { letterSpacing: "0.10em" }],
      },
      transitionTimingFunction: {
        out: "var(--ease-out)",
      },
      animation: {
        'pulse-slow': 'pulse 3s cubic-bezier(0.4, 0, 0.6, 1) infinite',
        'fade': 'fadeIn 0.4s var(--ease-out)',
        // mobile shell: the nav drawer flies in from the edge, sheets rise
        'slide-in-left': 'slideInLeft 0.24s var(--ease-out)',
        'sheet-up': 'sheetUp 0.26s var(--ease-out)',
      },
      keyframes: {
        fadeIn: {
          from: { opacity: '0' },
          to: { opacity: '1' },
        },
        slideInLeft: {
          from: { transform: 'translateX(-100%)' },
          to: { transform: 'translateX(0)' },
        },
        sheetUp: {
          from: { transform: 'translateY(12px)', opacity: '0' },
          to: { transform: 'translateY(0)', opacity: '1' },
        },
      },
    },
  },
  plugins: [],
}
