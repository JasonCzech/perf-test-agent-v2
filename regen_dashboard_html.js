const fs = require('fs');
const path = require('path');

const rootDir = __dirname;
const jsxPath = path.join(rootDir, 'perf_test_dashboard.jsx');
const htmlPath = path.join(rootDir, 'perf_test_dashboard.html');

const jsxText = fs.readFileSync(jsxPath, 'utf8');
const jsxLines = jsxText.split('\n');

// Remove the module import line because HTML uses React from global script tags.
if (/^import\s+\{\s*useState\b/.test(jsxLines[0] || '')) {
  jsxLines.shift();
}

let appCode = jsxLines.join('\n');
appCode = appCode.replace(/^export\s+default\s+function\s+App\s*\(\)\s*\{/m, 'function App(){');
appCode = appCode.replace(/^export\s+default\s+App\s*;\s*$/m, '');

const html = [
  '<!DOCTYPE html>',
  '<html lang="en">',
  '<head>',
  '  <meta charset="UTF-8" />',
  '  <meta name="viewport" content="width=device-width, initial-scale=1" />',
  '  <title>Perf Test Agent Dashboard</title>',
  '  <script crossorigin src="https://cdn.jsdelivr.net/npm/react@18/umd/react.production.min.js"></script>',
  '  <script crossorigin src="https://cdn.jsdelivr.net/npm/react-dom@18/umd/react-dom.production.min.js"></script>',
  '  <script crossorigin src="https://cdn.jsdelivr.net/npm/@babel/standalone/babel.min.js"></script>',
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

fs.writeFileSync(htmlPath, html);
console.log('Regenerated perf_test_dashboard.html from perf_test_dashboard.jsx');
