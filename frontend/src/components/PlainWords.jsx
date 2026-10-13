// PlainWords.jsx - "In plain words": a few sentences that sum up the best setup.
//
// NOTE: the text comes from the backend (routes/explain.py). When Amazon Bedrock is set up it writes the
// sentences, and the backend has already checked that every number in them is one the simulator produced.
// If that check fails, or Bedrock is not set up, the backend sends its built-in sentences instead.
// The label says which one you are reading, so nobody is told an AI wrote something it did not.

import Icon from './Icon'

export default function PlainWords({ state }) {
  if (state.status === 'error') return null // the card is extra; say nothing rather than show a broken box
  const data = state.status === 'ok' ? state.data : null
  const bedrock = data?.source === 'bedrock'

  return (
    <section className="card plain" aria-labelledby="plain-title" aria-busy={state.status === 'loading'}>
      <h2 id="plain-title" className="card-title"><Icon name="spark" size={18} /> In plain words</h2>
      {data ? (
        <>
          <p className="plain-text">{data.text}</p>
          <p className="card-note">
            {bedrock
              ? 'Written by Amazon Bedrock. Every number was checked against the simulation.'
              : 'Built-in summary. Every number comes straight from the simulation.'}
          </p>
        </>
      ) : (
        <p className="card-note">Writing the summary...</p>
      )}
    </section>
  )
}
