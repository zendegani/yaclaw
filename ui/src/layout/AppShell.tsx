import { NavLink, Outlet } from "react-router-dom";
import { Activity, LayoutDashboard, MessageSquare, Settings } from "lucide-react";
import { cn } from "@/lib/utils";
import { useChatState } from "@/state/store";

const NAV = [
  { to: "/", label: "Chat", icon: MessageSquare, end: true },
  { to: "/trace", label: "Trace", icon: Activity },
  { to: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
  { to: "/settings", label: "Settings", icon: Settings },
];

export function AppShell() {
  const { status } = useChatState();
  return (
    <div className="dark flex h-full bg-background text-foreground">
      <aside className="flex w-56 shrink-0 flex-col border-r border-sidebar-border bg-sidebar text-sidebar-foreground">
        <div className="flex h-14 items-center gap-2 px-4 font-semibold tracking-tight">
          <span className="inline-block h-2.5 w-2.5 rounded-full bg-emerald-400" />
          Pishkar
        </div>
        <nav className="flex flex-1 flex-col gap-1 px-2">
          {NAV.map(({ to, label, icon: Icon, end }) => (
            <NavLink
              key={to}
              to={to}
              end={end}
              className={({ isActive }) =>
                cn(
                  "flex items-center gap-2 rounded-md px-3 py-2 text-sm transition-colors",
                  isActive
                    ? "bg-sidebar-accent text-sidebar-accent-foreground"
                    : "text-muted-foreground hover:bg-sidebar-accent/60 hover:text-sidebar-accent-foreground",
                )
              }
            >
              <Icon className="h-4 w-4" />
              {label}
            </NavLink>
          ))}
        </nav>
        <div className="border-t border-sidebar-border p-3 text-xs text-muted-foreground">
          <div className="flex items-center gap-2">
            <span
              className={cn(
                "inline-block h-2 w-2 rounded-full",
                status === "open" && "bg-emerald-400",
                status === "connecting" && "bg-amber-400",
                status === "closed" && "bg-rose-400",
              )}
            />
            <span className="capitalize">{status}</span>
          </div>
        </div>
      </aside>
      <main className="flex flex-1 flex-col overflow-hidden">
        <Outlet />
      </main>
    </div>
  );
}
