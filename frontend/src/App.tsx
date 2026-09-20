import { Navigate, Route, Routes } from 'react-router-dom';

import { AuthProvider, useAuth } from './auth/AuthContext';
import { Layout } from './components/Layout';
import { ClaimDetail } from './pages/ClaimDetail';
import { Claims } from './pages/Claims';
import { NewClaim } from './pages/NewClaim';
import { PrivacyHelp } from './pages/PrivacyHelp';
import { Profile } from './pages/Profile';
import { SignIn } from './pages/SignIn';

function RequireAuth({ children }: { children: React.ReactNode }) {
  const { email, loading } = useAuth();

  if (loading) {
    return <p className="text-sm text-neutral-500">Loading…</p>;
  }
  if (!email) {
    return <Navigate to="/signin" replace />;
  }
  return <>{children}</>;
}

function Routing() {
  const { email, loading } = useAuth();

  return (
    <Layout>
      <Routes>
        <Route
          path="/signin"
          element={!loading && email ? <Navigate to="/claims" replace /> : <SignIn />}
        />
        <Route
          path="/claims"
          element={
            <RequireAuth>
              <Claims />
            </RequireAuth>
          }
        />
        <Route
          path="/claims/new"
          element={
            <RequireAuth>
              <NewClaim />
            </RequireAuth>
          }
        />
        <Route
          path="/claims/:claimId"
          element={
            <RequireAuth>
              <ClaimDetail />
            </RequireAuth>
          }
        />
        <Route
          path="/profile"
          element={
            <RequireAuth>
              <Profile />
            </RequireAuth>
          }
        />
        <Route path="/privacy" element={<PrivacyHelp />} />
        <Route path="*" element={<Navigate to="/claims" replace />} />
      </Routes>
    </Layout>
  );
}

export default function App() {
  return (
    <AuthProvider>
      <Routing />
    </AuthProvider>
  );
}
