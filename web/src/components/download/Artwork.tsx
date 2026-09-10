/** `title` is the native tooltip: the library table hangs the release a track came off here, because the
    cover is the one cell with room for it and the row's own columns already spend their width on the
    fields that sort. */
export default function Artwork({ url, rejected, small, title }: { url: string | null; rejected?: boolean; small?: boolean; title?: string }) {
  const cls = `artwork${small ? ' sm' : ''}`
  if (rejected) return <div className={`${cls} rejected`} aria-hidden>×</div>
  return url ? <img className={cls} src={url} alt="" title={title} /> : <div className={cls} title={title} aria-hidden={title ? undefined : true} />
}
