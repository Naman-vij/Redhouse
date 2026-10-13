// Icon.jsx - the small line icons used by the sidebar, buttons and cards. One place, so they all share a style.
// Each icon is a 24x24 drawing made of strokes. They carry no meaning on their own: the words beside them do,
// so they are hidden from screen readers.

const PATHS = {
  logo: (<><path d="M3 11.5 12 4l9 7.5" /><path d="M5.5 10v9.5h13V10" /><path d="M10 19.5v-5h4v5" /></>),
  plan: (<><rect x="3.5" y="4" width="17" height="16" rx="2.5" /><path d="M3.5 9.5h17M9.5 9.5V20" /></>),
  camera: (<><path d="M3.5 8.5h11a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-11z" /><path d="m16.5 12 4-2.5v7L16.5 14" /></>),
  intruder: (<><path d="M12 3.5 4.5 6.5v5.2c0 4.3 3 7.3 7.5 8.8 4.5-1.5 7.5-4.5 7.5-8.8V6.5z" /><path d="M12 9v4M12 15.5v.01" /></>),
  chart: (<><path d="M5 20V11M12 20V4M19 20v-6" /></>),
  bulb: (<><path d="M9 18h6M10 21h4" /><path d="M12 3.5a6 6 0 0 0-3.5 10.9c.6.5 1 1.2 1 2V17h5v-.6c0-.8.4-1.5 1-2A6 6 0 0 0 12 3.5z" /></>),
  file: (<><path d="M6.5 3.5h7l4 4V20.5h-11z" /><path d="M13.5 3.5v4h4M9 12.5h6M9 16h6" /></>),
  upload: (<><path d="M12 15.5V4.5M7.5 9 12 4.5 16.5 9" /><path d="M4.5 15.5v3a1.5 1.5 0 0 0 1.5 1.5h12a1.5 1.5 0 0 0 1.5-1.5v-3" /></>),
  ring: (<><path d="M19 12a7 7 0 1 1-2.2-5.1" /><path d="M19.5 4.5v4h-4" /></>),
  search: (<><circle cx="11" cy="11" r="6.5" /><path d="m16 16 4 4" /></>),
  chevron: (<><path d="m6 9 6 6 6-6" /></>),
  alert: (<><path d="M12 4 2.8 19.5h18.4z" /><path d="M12 10v4.5M12 17.2v.01" /></>),
  check: (<><path d="m5 12.5 4.5 4.5L19 7.5" /></>),
  left: (<><path d="M5 12a7 7 0 1 0 2.2-5.1" /><path d="M4.5 4.5v4h4" /></>),
  right: (<><path d="M19 12a7 7 0 1 1-2.2-5.1" /><path d="M19.5 4.5v4h-4" /></>),
  pin: (<><path d="M12 21s6.5-5.8 6.5-11A6.5 6.5 0 0 0 5.5 10c0 5.2 6.5 11 6.5 11z" /><circle cx="12" cy="10" r="2.3" /></>),
  download: (<><path d="M12 4v11M7.5 10.5 12 15l4.5-4.5" /><path d="M4.5 16v2.5A1.5 1.5 0 0 0 6 20h12a1.5 1.5 0 0 0 1.5-1.5V16" /></>),
  mail: (<><rect x="3.5" y="5.5" width="17" height="13" rx="2" /><path d="m4 7 8 6 8-6" /></>),
  reset: (<><path d="M4.5 12a7.5 7.5 0 1 0 2.4-5.5" /><path d="M4.5 4.5v4.5H9" /></>),
  plus: (<><path d="M12 5v14M5 12h14" /></>),
  spark: (<><path d="M12 3.5v4M12 16.5v4M3.5 12h4M16.5 12h4M6 6l2.5 2.5M15.5 15.5 18 18M18 6l-2.5 2.5M8.5 15.5 6 18" /></>),
  power: (<><path d="M12 3.5v8" /><path d="M6.8 7.2a7.5 7.5 0 1 0 10.4 0" /></>),
}

export default function Icon({ name, size = 20 }) {
  return (
    <svg
      className="icon"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      {PATHS[name]}
    </svg>
  )
}
