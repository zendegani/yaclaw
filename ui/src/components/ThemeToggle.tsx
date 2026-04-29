import { Monitor, Moon, Sun } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useTheme, type Theme } from "@/lib/theme";

const ORDER: Theme[] = ["system", "light", "dark"];
const ICONS: Record<Theme, React.ComponentType<{ className?: string }>> = {
  system: Monitor,
  light: Sun,
  dark: Moon,
};
const LABELS: Record<Theme, string> = {
  system: "System theme",
  light: "Light theme",
  dark: "Dark theme",
};

export function ThemeToggle() {
  const { theme, setTheme } = useTheme();
  const Icon = ICONS[theme];
  const next = ORDER[(ORDER.indexOf(theme) + 1) % ORDER.length];

  return (
    <Button
      variant="ghost"
      size="icon"
      onClick={() => setTheme(next)}
      title={`${LABELS[theme]} (click for ${LABELS[next].toLowerCase()})`}
      className="h-7 w-7"
    >
      <Icon className="h-3.5 w-3.5" />
    </Button>
  );
}
