import { ExternalLink } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { fetchManageLinks } from "@/api/client";
import { manageUrl } from "@/lib/manageLinks";

/** Opens the service's own account page; the app itself makes no request. */
export function ManageLink({ merchant }: { merchant: string | null }) {
  const links = useQuery({ queryKey: ["manage-links"], queryFn: fetchManageLinks, staleTime: Infinity });
  const href = manageUrl(merchant, links.data);
  if (!href) return <span className="text-label text-muted-foreground">No manage link yet</span>;
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      aria-label={`Manage ${merchant} (opens in a new tab)`}
      className="inline-flex min-h-8 items-center gap-1.5 rounded-full bg-active px-3 text-label font-semibold transition-transform duration-150 ease-out active:scale-[0.97]"
    >
      Manage
      <ExternalLink className="size-3" aria-hidden="true" />
    </a>
  );
}
