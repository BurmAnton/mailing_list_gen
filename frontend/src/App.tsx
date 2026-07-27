import { BrowserRouter, Link, Route, Routes } from 'react-router-dom';
import { CampaignDetailPage } from './pages/CampaignDetailPage';
import { CampaignListPage } from './pages/CampaignListPage';
import './index.css';

export default function App() {
  return (
    <BrowserRouter>
      <div className="app-shell">
        <header className="app-header">
          <Link to="/" className="brand" style={{ textDecoration: 'none' }}>
            Mail<span>Craft</span>
          </Link>
          <Link className="nav-link" to="/">
            Кампании
          </Link>
        </header>
        <main>
          <Routes>
            <Route path="/" element={<CampaignListPage />} />
            <Route path="/campaigns/:id/:tab?" element={<CampaignDetailPage />} />
          </Routes>
        </main>
      </div>
    </BrowserRouter>
  );
}
