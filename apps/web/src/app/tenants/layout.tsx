import { RequireAuth, Shell } from "@/components/session";

export default function TenantsLayout({ children }: LayoutProps<"/tenants">) {
  return (
    <RequireAuth>
      <Shell>{children}</Shell>
    </RequireAuth>
  );
}
