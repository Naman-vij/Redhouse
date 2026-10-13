// ReportCard.jsx - "Reports": the PDF of everything on this page, to download or to have emailed.
//
// The email goes to the one address typed here, for this one report. The server does not keep the address.
// If email is not set up, or the email service refuses, the message says so and the download still works.

import { useState } from 'react'
import { emailReport } from '../api.js'
import Icon from './Icon'

export default function ReportCard({ reportHref, archetype, site }) {
  const [email, setEmail] = useState('')
  const [mail, setMail] = useState({ status: 'idle', message: '' }) // idle | sending | sent | error

  async function send(event) {
    event.preventDefault()
    setMail({ status: 'sending', message: '' })
    try {
      await emailReport(archetype, site, email.trim())
      setMail({ status: 'sent', message: `Sent. Check ${email.trim()} (and spam) in a minute.` })
    } catch (err) {
      setMail({ status: 'error', message: err.message || 'The email could not be sent. Use the download button instead.' })
    }
  }

  return (
    <section className="card report-card" id="reports" aria-labelledby="report-title">
      <h2 id="report-title" className="card-title">Report</h2>
      <p className="card-sub">
        A PDF with today and after the suggested changes, place by place, with maps, the camera-failure table and the
        summary above. Simulation only.
      </p>

      {reportHref ? (
        <>
          <p className="report-actions">
            <a className="button button-primary" href={reportHref} download>
              <Icon name="download" size={16} /> Download the report (PDF)
            </a>
          </p>
          <form className="report-email" onSubmit={send}>
            <label htmlFor="report-email">Or email it to</label>
            <input
              id="report-email"
              type="email"
              required
              autoComplete="email"
              placeholder="you@example.com"
              value={email}
              onChange={(e) => { setEmail(e.target.value); if (mail.status !== 'sending') setMail({ status: 'idle', message: '' }) }}
            />
            <button className="button" type="submit" disabled={mail.status === 'sending'}>
              <Icon name="mail" size={16} /> {mail.status === 'sending' ? 'Sending...' : 'Email the report'}
            </button>
            <p className={`card-note report-note${mail.status === 'error' ? ' is-error' : ''}${mail.status === 'sent' ? ' is-sent' : ''}`} role="status" aria-live="polite">
              {mail.message || 'Used once to send this report. It is not saved.'}
            </p>
          </form>
        </>
      ) : (
        <p className="card-note">The report is ready once the best setup has been worked out.</p>
      )}
    </section>
  )
}
