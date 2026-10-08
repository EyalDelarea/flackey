import React from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import './theme.css'
import { insetTitlebar } from './platform'
import { installZoom } from './zoom'
import { adoptToken } from './session'
import { api } from './api'

adoptToken()
if (insetTitlebar()) document.documentElement.classList.add('native')
installZoom()

const root = createRoot(document.getElementById('root')!)
// The session cookie first: the event stream, artwork and audio all open on the first render and none of
// them can send the token. A refusal still renders, and the page then says what it is missing.
api.session().catch(() => undefined).finally(() => root.render(<React.StrictMode><App /></React.StrictMode>))
