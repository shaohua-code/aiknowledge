export default function BrandMark({ large = false }: { large?: boolean }) {
  return (
    <span className={`brand-mark ${large ? 'large' : ''}`} aria-hidden="true">
      <svg viewBox="0 0 32 32" role="presentation">
        <path d="M16 3.5 27 9.8 16 16 5 9.8 16 3.5Z" />
        <path d="m5 15.6 11 6.3 11-6.3" />
        <path d="m5 21.7 11 6.2 11-6.2" />
      </svg>
    </span>
  )
}
