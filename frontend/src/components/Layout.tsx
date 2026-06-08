import { NavLink, Outlet } from "react-router-dom";

import { useAuth } from "../auth/AuthContext";
import { Button } from "./Button";
import styles from "./Layout.module.css";

function navClass({ isActive }: { isActive: boolean }) {
  return isActive ? `${styles.navLink} ${styles.active}` : styles.navLink;
}

/** App shell: a top bar (brand, nav, user) over the routed page content. */
export function Layout() {
  const { user, logout } = useAuth();
  return (
    <div className={styles.shell}>
      <header className={styles.header}>
        <div className={styles.brand}>Agent Control Plane</div>
        <nav className={styles.nav}>
          <NavLink to="/" end className={navClass}>
            Runs
          </NavLink>
          <NavLink to="/schedules" className={navClass}>
            Schedules
          </NavLink>
          <NavLink to="/workflows" className={navClass}>
            Workflows
          </NavLink>
          <NavLink to="/agents" className={navClass}>
            Agents
          </NavLink>
        </nav>
        <div className={styles.user}>
          {user && <span className={styles.username}>{user.username}</span>}
          <Button variant="secondary" onClick={() => void logout()}>
            Log out
          </Button>
        </div>
      </header>
      <main className={styles.main}>
        <Outlet />
      </main>
    </div>
  );
}
