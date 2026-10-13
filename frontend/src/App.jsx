// App.jsx - decides which page to show:
//   ?new          the upload wizard
//   ?site=<id>    the results for an uploaded site
//   (nothing)     the results for the built-in demo house
//
// Dashboard is keyed by the site, so switching site starts it fresh: no state leaks from one site
// to the next.

import Dashboard from './Dashboard'
import UploadWizard from './UploadWizard'
import { useLocation } from './lib/useLocation'

export default function App() {
  const [params, navigate] = useLocation()
  const site = params.get('site')

  if (params.has('new')) {
    return (
      <UploadWizard
        onCancel={() => navigate(site ? `?site=${encodeURIComponent(site)}` : '')}
        onOpen={(id) => navigate(id ? `?site=${encodeURIComponent(id)}` : '')}
      />
    )
  }
  return <Dashboard key={site ?? 'demo'} site={site} onUpload={() => navigate('?new')} onHome={() => navigate('')} />
}
