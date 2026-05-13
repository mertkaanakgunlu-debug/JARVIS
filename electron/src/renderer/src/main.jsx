import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import Widget from './Widget'

const mode = new URLSearchParams(window.location.search).get('mode')

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    {mode === 'widget' ? <Widget /> : <App />}
  </React.StrictMode>
)
