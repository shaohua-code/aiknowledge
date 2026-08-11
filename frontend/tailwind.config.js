/** @type {import('tailwindcss').Config} */
export default {
  // 关闭 preflight，避免覆盖 antd 基础样式
  corePlugins: {
    preflight: false
  },
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        // 与 antd 主色对齐，便于 tailwind 类直接引用
        primary: '#1677ff'
      }
    }
  },
  plugins: []
}
