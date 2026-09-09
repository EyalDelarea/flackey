export default function Artwork({ url, rejected, small }: { url: string | null; rejected?: boolean; small?: boolean }) {
  const cls = `artwork${small ? ' sm' : ''}`
  if (rejected) return <div className={`${cls} rejected`} aria-hidden>×</div>
  return url ? <img className={cls} src={url} alt="" /> : <div className={cls} aria-hidden />
}
