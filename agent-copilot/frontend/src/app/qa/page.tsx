import type { Metadata } from "next";
import { QaExplorer } from "@/components/qa/QaExplorer";

export const metadata: Metadata = { title: "WellOps QA", description: "Eval runs, failure taxonomy and regression gate." };

export default function QaPage() { return <QaExplorer />; }
