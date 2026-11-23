import { useState } from 'react'
import { fireEvent, render, screen } from '@testing-library/react'
import HelpTag from './HelpTag'

function SelfDisabling() {
  const [busy, setBusy] = useState(false)
  return <HelpTag text="Tries again."><button disabled={busy} onClick={() => setBusy(true)}>{busy ? 'Retrying…' : 'Retry all 2'}</button></HelpTag>
}

it('closes when its button is pressed, since a button that disables itself never fires blur', () => {
  render(<SelfDisabling />)
  const button = screen.getByRole('button', { name: 'Retry all 2' })
  fireEvent.focus(button)
  expect(screen.getByRole('tooltip')).toHaveTextContent('Tries again.')
  fireEvent.click(button)       // what Enter or Space on a focused button dispatches
  expect(screen.getByRole('button', { name: 'Retrying…' })).toBeDisabled()
  expect(screen.queryByRole('tooltip')).not.toBeInTheDocument()
})
