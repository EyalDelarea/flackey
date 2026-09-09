import React from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import './theme.css'
import { insetTitlebar } from './platform'
import { installZoom } from './zoom'

if (insetTitlebar()) document.documentElement.classList.add('native')
installZoom()

createRoot(document.getElementById('root')!).render(<React.StrictMode><App /></React.StrictMode>)
