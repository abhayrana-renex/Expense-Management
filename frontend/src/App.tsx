import type { ReactNode } from "react";
import { Navigate, Route, Routes, useParams } from "react-router-dom";
import { useAuth } from "./auth/AuthContext";
import { Layout } from "./components/Layout";
import { Skeleton } from "./components/ui";
import { Approvals } from "./pages/Approvals";
import { ChangePassword } from "./pages/ChangePassword";
import { ExpenseDetail } from "./pages/ExpenseDetail";
import { ExpenseForm } from "./pages/ExpenseForm";
import { Expenses } from "./pages/Expenses";
import { Home } from "./pages/Home";
import { Login } from "./pages/Login";
import { Payments } from "./pages/Payments";
import { Reports } from "./pages/Reports";
import { Roles } from "./pages/admin/Roles";
import { Audit } from "./pages/admin/Audit";
import { BankApprovals } from "./pages/admin/BankApprovals";
import { Masters } from "./pages/admin/Masters";
import { Rules } from "./pages/admin/Rules";
import { Users } from "./pages/admin/Users";
import { VendorDetail } from "./pages/admin/VendorDetail";

function Protected({ children, permission, anyOf }: { children: ReactNode; permission?: string;
  anyOf?: string[] }) {
  const { me, loading, can } = useAuth();
  if (loading) return <Skeleton rows={6} />;
  if (!me) return <Navigate to="/login" replace />;
  if (me.must_change_password || me.password_expired) return <Navigate to="/change-password" replace />;
  if (permission && !can(permission)) return <Navigate to="/" replace />;
  if (anyOf && !anyOf.some((p) => can(p))) return <Navigate to="/" replace />;
  return <>{children}</>;
}

/** Remount the form per expense so state never leaks between records. */
function KeyedForm() {
  const { id } = useParams();
  return <ExpenseForm key={id ?? "new"} />;
}

export default function App() {
  const { me, loading } = useAuth();
  return (
    <Routes>
      <Route path="/login" element={me && !loading ? <Navigate to="/" replace /> : <Login />} />
      <Route path="/change-password" element={
        loading ? <Skeleton /> : !me ? <Navigate to="/login" replace />
          : me.must_change_password || me.password_expired ? <ChangePassword />
            : <Navigate to="/account/password" replace />} />
      <Route element={<Protected><Layout /></Protected>}>
        <Route index element={<Home />} />
        <Route path="expenses" element={<Expenses />} />
        <Route path="expenses/new" element={<Protected permission="expense.create"><KeyedForm /></Protected>} />
        <Route path="expenses/:id" element={<ExpenseDetail />} />
        <Route path="expenses/:id/edit" element={<KeyedForm />} />
        <Route path="approvals" element={<Protected permission="expense.approve"><Approvals /></Protected>} />
        <Route path="reports" element={<Protected permission="report.read"><Reports /></Protected>} />
        <Route path="payments" element={<Protected anyOf={["payment.schedule", "payment.approve",
          "payment.record"]}><Payments /></Protected>} />
        <Route path="admin/roles" element={<Protected permission="user.manage"><Roles /></Protected>} />
        <Route path="account/password" element={<ChangePassword />} />
        <Route path="admin/users" element={<Protected permission="user.manage"><Users /></Protected>} />
        <Route path="admin/masters" element={<Protected permission="master.manage"><Masters /></Protected>} />
        <Route path="masters/vendors/:id" element={<Protected permission="master.read"><VendorDetail /></Protected>} />
        <Route path="admin/bank-approvals" element={<Protected permission="vendor_bank.approve"><BankApprovals /></Protected>} />
        <Route path="admin/rules" element={<Protected permission="approval_rule.manage"><Rules /></Protected>} />
        <Route path="admin/audit" element={<Protected permission="audit.read"><Audit /></Protected>} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
