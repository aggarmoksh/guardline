import { redirect } from "next/navigation";

// QR codes point at the site root: go straight to the phone line.
export default function Home() {
  redirect("/call");
}
