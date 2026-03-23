const fs = require('fs');

const jsx = fs.readFileSync('perf_test_dashboard.jsx', 'utf8').split('\n');
jsx.shift();
const appCode = jsx.join('\n').replace(/^export default function App\(\)/m, 'function App()');

const html = [
  '<!DOCTYPE html>',
  '<html lang="en">',
  '<head>',
  '  <meta charset="UTF-8" />',
  '  <meta name="viewport" content="width=device-width, initial-scale=1" />',
  '  <title>Perf Test Agent Dashboard</title>',
  '  <script crossorigin src="https://unpkg.com/react@18/umd/react.production.min.js"></script>',
  '  <script crossorigin src="https://unpkg.com/react-dom@18/umd/react-dom.production.min.js"></script>',
  '  <script crossorigin src="https://unpkg.com/@babel/standalone/babel.min.js"></script>',
  '  <link rel="preconnect" href="https://fonts.googleapis.com" />',
  '  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />',
  '  <link href="https://fonts.googleapis.com/css2?family=Nunito:wght@400;600;700;800&family=IBM+Plex+Mono:wght@400;600;700&display=swap" rel="stylesheet" />',
  '</head>',
  '<body>',
  '  <div id="root"></div>',
  '  <script type="text/babel" data-presets="react">',
  'const { useState, useEffect, useRef, useCallback, useMemo } = React;',
  appCode,
  '',
  'const root = ReactDOM.createRoot(document.getElementById("root"));',
  'root.render(<App />);',
  '  </script>',
  '</body>',
  '</html>',
  '',
].join('\n');

fs.writeFileSync('perf_test_dashboard.html', html);
