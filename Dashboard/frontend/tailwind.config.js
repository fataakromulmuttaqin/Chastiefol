/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        brand: { 500: '#7C3AED', 600: '#6D28D9' },
        profit: '#00C853',
        loss: '#FF1744',
      },
    },
  },
  plugins: [],
}
