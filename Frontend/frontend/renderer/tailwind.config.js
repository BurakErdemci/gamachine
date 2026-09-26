const colors = require('tailwindcss/colors')

module.exports = {
    darkMode: ['class'],
    content: [
    './renderer/pages/**/*.{js,ts,jsx,tsx}',
    './renderer/components/**/*.{js,ts,jsx,tsx}',
    // Shared class maps live here too (convFamily's STATUS_DOT).
    './renderer/lib/**/*.{js,ts,jsx,tsx}',
  ],
  theme: {
    colors: {
      transparent: 'transparent',
      current: 'currentColor',
      black: colors.black,
      white: colors.white,
      gray: colors.gray,
      slate: colors.slate,
      blue: colors.blue,
      indigo: colors.indigo,
      green: colors.green,
      emerald: colors.emerald,
      amber: colors.amber,
      orange: colors.orange,
      red: colors.red,
      yellow: colors.yellow,
      violet: colors.violet,
      purple: colors.purple,
      cyan: colors.cyan,
    },
    extend: {},
  },
  plugins: [],
}
