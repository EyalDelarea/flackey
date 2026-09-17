export const FORMAT_LABELS: Record<string, string> = { aiff: 'AIFF', wav: 'WAV', flac: 'FLAC' }

export function formatNote(format: string): string {
  if (format === 'aiff') return 'Recommended for Rekordbox: uncompressed audio, rich tags, and artwork.'
  if (format === 'wav') return 'Uncompressed audio with Rekordbox-readable text tags. Cover art is not preserved there.'
  if (format === 'flac') return 'Smaller lossless files with tags and artwork, supported by Rekordbox.'
  return 'Lossless filing format.'
}

export default function FormatOptions({ formats, value, onChange, disabled = false, legend = 'File format' }: {
  formats: string[]; value: string; onChange: (format: string) => void; disabled?: boolean; legend?: string
}) {
  return (<fieldset className="format-options" aria-label={legend}>
    {formats.map(format => {
      const selected = format === value
      return <button key={format} type="button" className={`format-choice${selected ? ' selected' : ''}`}
        aria-pressed={selected} onClick={() => onChange(format)} disabled={disabled}>
        <span className="format-name">{FORMAT_LABELS[format] ?? format.toUpperCase()}</span>
        {format === 'aiff' && <span className="format-badge">Default</span>}
        <span className="format-note">{formatNote(format)}</span>
      </button>
    })}
  </fieldset>)
}
