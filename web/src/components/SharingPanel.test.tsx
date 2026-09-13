import { render, screen, fireEvent } from '@testing-library/react'
import SharingPanel from './SharingPanel'
import type { SharingState } from '../api'

const state = (over: Partial<SharingState> = {}): SharingState => ({ port: 50300, enabled: true, checking: false, mapping: null,
  reachable: null, public_ip: null, lan_ip: null, gateway: null, checked_at: null, error: null, ...over })

it('says people can download when the port is reachable', () => {
  render(<SharingPanel state={state({ reachable: true, mapping: 'natpmp' })} onCheck={vi.fn()} />)
  expect(screen.getByText('Other Soulseek users can download from you.')).toBeInTheDocument()
})

it('explains the forward when the port is closed, with the addresses it knows', () => {
  render(<SharingPanel state={state({ reachable: false, lan_ip: '10.0.0.5', gateway: '10.0.0.1', public_ip: '1.2.3.4' })} onCheck={vi.fn()} />)
  expect(screen.getByText(/Your Soulseek port is closed/)).toBeInTheDocument()
  expect(screen.getByText(/forward TCP port 50300 on your router to this Mac \(10\.0\.0\.5\)/)).toBeInTheDocument()
  expect(screen.getByText(/Your router's address is 10\.0\.0\.1/)).toBeInTheDocument()
  expect(screen.getByText(/exit address \(1\.2\.3\.4\)/)).toBeInTheDocument()
})

it('leaves out the addresses it does not know rather than printing a gap', () => {
  render(<SharingPanel state={state({ reachable: false })} onCheck={vi.fn()} />)
  expect(screen.getByText(/forward TCP port 50300 on your router to this Mac\./)).toBeInTheDocument()
  expect(screen.queryByText(/Your router's address/)).not.toBeInTheDocument()
  expect(screen.queryByText(/exit address \(/)).not.toBeInTheDocument()
})

it('compact mode keeps the sentence and points at Settings', () => {
  render(<SharingPanel state={state({ reachable: false })} onCheck={vi.fn()} compact />)
  expect(screen.getByText(/Your Soulseek port is closed/)).toBeInTheDocument()
  expect(screen.getByText(/Settings › Sharing/)).toBeInTheDocument()
  expect(screen.queryByText(/forward TCP port/)).not.toBeInTheDocument()
  expect(screen.queryByText('Check again')).not.toBeInTheDocument()
})

it('Check again calls onCheck and is disabled while checking', () => {
  const onCheck = vi.fn()
  const { rerender } = render(<SharingPanel state={state()} onCheck={onCheck} />)
  fireEvent.click(screen.getByText('Check again'))
  expect(onCheck).toHaveBeenCalled()
  rerender(<SharingPanel state={state({ checking: true })} onCheck={onCheck} />)
  expect(screen.getByText('Checking…')).toBeDisabled()
  expect(screen.getByText('Checking whether other people can reach you…')).toBeInTheDocument()
})

it('keeps the forward instructions up while a re-check is running', () => {
  render(<SharingPanel state={state({ checking: true, reachable: false, port: 50300,
    lan_ip: '192.168.1.10', gateway: '192.168.1.1' })} onCheck={vi.fn()} />)
  expect(screen.getByText('Checking whether other people can reach you…')).toBeInTheDocument()
  expect(screen.getByText(/50300/)).toBeInTheDocument()
})

it('says nothing has been checked yet rather than claiming the port is shut', () => {
  render(<SharingPanel state={state()} onCheck={vi.fn()} />)
  expect(screen.getByText('Not checked yet.')).toBeInTheDocument()
})

it('shows the reason the check itself failed, when there is one', () => {
  render(<SharingPanel state={state({ error: 'Something went wrong checking the port.' })} onCheck={vi.fn()} />)
  expect(screen.getByText('Something went wrong checking the port.')).toBeInTheDocument()
})

it('falls back to the unchecked line before any state has arrived', () => {
  render(<SharingPanel state={null} onCheck={vi.fn()} />)
  expect(screen.getByText('Not checked yet.')).toBeInTheDocument()
})
