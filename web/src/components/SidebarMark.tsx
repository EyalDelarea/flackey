/* The record from the app icon, alone and oversized, bleeding off the bottom-left corner of the sidebar.
   Not the icon shrunk down -- a shrunken icon reads as a second, competing logo; one motif at low opacity
   reads as texture. It uses currentColor throughout so it takes the sidebar's own ink in either theme,
   and `aria-hidden` because it says nothing a screen reader needs. */
export default function SidebarMark() {
  return (
    <svg className="sidebar-mark" viewBox="0 0 64 64" aria-hidden="true" focusable="false">
      <circle cx="32" cy="32" r="30" fill="none" stroke="currentColor" strokeWidth="1.4" />
      <circle cx="32" cy="32" r="23.5" fill="none" stroke="currentColor" strokeWidth="1" />
      <circle cx="32" cy="32" r="17.5" fill="none" stroke="currentColor" strokeWidth="1" />
      <circle cx="32" cy="32" r="11.2" fill="currentColor" fillOpacity="0.5" />
      <circle cx="32" cy="32" r="1.6" fill="none" stroke="currentColor" strokeWidth="1.4" />
    </svg>
  )
}
