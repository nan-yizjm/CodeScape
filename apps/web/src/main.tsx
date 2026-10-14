import { createRoot } from 'react-dom/client';
import App from './App';
import { ResourceBoundary } from './ResourceBoundary';
import '@xyflow/react/dist/style.css';
import './style.css';

createRoot(document.getElementById('root')!).render(<ResourceBoundary fullPage><App /></ResourceBoundary>);
