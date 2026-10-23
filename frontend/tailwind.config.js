/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      fontFamily: {
        serif: ['Newsreader', 'Georgia', 'serif'],
        sans: ['Inter', 'system-ui', '-apple-system', 'sans-serif'],
        mono: ['"JetBrains Mono"', 'ui-monospace', 'monospace'],
      },
      colors: {
        // Editorial Warm Ivory + Charcoal + Botanical Olive palette
        bg:             '#F7F5F0', // warm ivory parchment background
        surface:        '#FFFFFF', // pure paper surface for crisp contrast
        'surface-sub':  '#FAF8F5', // secondary warm off-white surface
        ink:            '#191816', // deep charcoal text
        muted:          '#5E5953', // warm secondary charcoal-grey (4.5:1+ contrast)
        subtle:         '#8C857B', // tertiary taupe / metadata
        border:         '#E3DDD3', // primary warm border
        'border-sub':   '#EDE8DE', // hairline interior border
        accent:         '#4E5B36', // refined botanical olive
        'accent-hover': '#3E492B', // deep olive on hover/focus
        'accent-muted': '#EFF2EA', // pale olive wash for highlights & drag states
        'accent-border':'#BFCBAA', // soft olive border
        success:        '#3D683A', // organic forest success green
        'success-muted':'#EEF4EC', // delicate success background
        'success-border':'#C4DBC1',// soft success border
        error:          '#9E362A', // restrained terracotta error red
        'error-muted':  '#FDF4F2', // warm muted error wash
        'error-border': '#EAC8C2', // integrated error border
      },
      boxShadow: {
        card: '0 1px 2px rgba(25,24,22,0.03), 0 6px 16px -2px rgba(25,24,22,0.05)',
        elevated: '0 4px 12px rgba(25,24,22,0.06), 0 20px 32px -4px rgba(25,24,22,0.08)',
        subtle: '0 1px 2px rgba(25,24,22,0.04)',
      },
      transitionTimingFunction: {
        'out-strong': 'cubic-bezier(0.23, 1, 0.32, 1)',
        'in-out-strong': 'cubic-bezier(0.77, 0, 0.175, 1)',
      },
      transitionDuration: {
        '150': '150ms',
        '200': '200ms',
        '250': '250ms',
      },
      keyframes: {
        fadeSlideUp: {
          '0%':   { opacity: '0', transform: 'translateY(6px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
        spinSlow: {
          '0%':   { transform: 'rotate(0deg)' },
          '100%': { transform: 'rotate(360deg)' },
        },
        pulseSoft: {
          '0%, 100%': { opacity: '1' },
          '50%':      { opacity: '0.4' },
        },
        checkIn: {
          '0%':   { strokeDashoffset: '24', opacity: '0' },
          '60%':  { opacity: '1' },
          '100%': { strokeDashoffset: '0', opacity: '1' },
        },
      },
      animation: {
        'fade-slide-up': 'fadeSlideUp 220ms cubic-bezier(0.23,1,0.32,1) forwards',
        'spin-slow':     'spinSlow 0.9s linear infinite',
        'pulse-soft':    'pulseSoft 2s ease-in-out infinite',
        'check-in':      'checkIn 380ms cubic-bezier(0.23,1,0.32,1) forwards',
      },
    },
  },
  plugins: [],
}
