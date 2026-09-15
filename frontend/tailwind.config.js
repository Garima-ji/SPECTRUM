/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        // Custom color palette for rich premium look
        brand: {
          50: '#f0f7ff',
          100: '#e0effe',
          200: '#bae2fd',
          300: '#7cc8fc',
          400: '#38a9f8',
          500: '#0e8eed',
          600: '#0270c9',
          700: '#0359a3',
          800: '#074c87',
          900: '#0c4070',
        }
      }
    },
  },
  plugins: [],
}
