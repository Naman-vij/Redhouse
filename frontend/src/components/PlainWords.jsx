// PlainWords.jsx - "In plain words": a few sentences that sum up the best setup.
//
// NOTE: the text comes from the backend (routes/explain.py). When Amazon Bedrock is set up it writes the
// sentences, and the backend has already checked that every number in them is one the simulator produced.
// If that check fails, or Bedrock is not set up, the backend sends its built-in sentences instead.
// The label says which one you are reading, so nobody is told an AI wrote something it did not.

import { useState } from 'react'
import { emailReport } from '../api.js'

export default function PlainWords({ state, reportHref, archetype, site }) {
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

  if (state.status === 'error' && !reportHref) return null // the card is extra; say nothing rather than show a broken box
  const data = state.status === 'ok' ? state.data : null
  const bedrock = data?.source === 'bedrock'

  return (
    <section className="plain" aria-labelledby="plain-title" aria-busy={state.status === 'loading'}>
      <h2 id="plain-title">In plain words</h2>
      {state.status === 'error' ? null : data ? (
        <>
          <p className="plain-text">{data.text}</p>
          <p className="plain-source">
            {bedrock
              ? 'Written by Amazon Bedrock. Every number was checked against the simulation.'
              : 'Built-in summary. Every number comes straight from the simulation.'}
          </p>
        </>
      ) : (
        <p className="plain-source">Writing the summary...</p>
      )}
      {reportHref && (
        <>
          <p className="plain-report">
            <a className="button" href={reportHref} download>Download the report (PDF)</a>
            <span className="plain-source"> Today and after the suggested changes, place by place, with maps.</span>
          </p>
          <form className="plain-email" onSubmit={send}>
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
              {mail.status === 'sending' ? 'Sending...' : 'Email the report'}
            </button>
            <p className={`plain-source plain-email-note${mail.status === 'error' ? ' is-error' : ''}`} role="status" aria-live="polite">
              {mail.message || 'Used once to send this report. It is not saved.'}
            </p>
          </form>
        </>
      )}
    </section>
  )
}
