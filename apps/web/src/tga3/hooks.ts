import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { tga3Api } from "./api";
import type { DialogueMessage } from "./types";

export function useTaskRuntime(taskId: string) {
  const client = useQueryClient();
  const task = useQuery({
    queryKey: ["tga3", "task", taskId],
    queryFn: () => tga3Api.getTask(taskId),
    refetchInterval: 1500,
  });
  const blackboard = useQuery({
    queryKey: ["tga3", "blackboard", taskId],
    queryFn: () => tga3Api.getBlackboard(taskId),
    refetchInterval: 2000,
  });
  const [messages, setMessages] = useState<DialogueMessage[]>([]);
  const [streamState, setStreamState] = useState<"connecting" | "online" | "offline">("connecting");

  useEffect(() => {
    let active = true;
    const merge = (items: DialogueMessage[]) => {
      if (!active) return;
      setMessages((current) => {
        const byId = new Map(current.map((item) => [item.id, item]));
        items.forEach((item) => byId.set(item.id, item));
        return [...byId.values()].sort((left, right) => left.seq - right.seq);
      });
    };
    const poll = () => tga3Api.getDialogue(taskId).then(merge).catch(() => setStreamState("offline"));
    void poll();
    const source = new EventSource(tga3Api.dialogueStreamUrl(taskId));
    source.onopen = () => setStreamState("online");
    source.onerror = () => setStreamState("offline");
    source.onmessage = (event) => {
      merge([JSON.parse(event.data) as DialogueMessage]);
      void client.invalidateQueries({ queryKey: ["tga3", "task", taskId] });
      void client.invalidateQueries({ queryKey: ["tga3", "blackboard", taskId] });
    };
    const pollingFallback = window.setInterval(() => void poll(), 2500);
    return () => {
      active = false;
      window.clearInterval(pollingFallback);
      source.close();
    };
  }, [client, taskId]);

  const pendingQuestion = useMemo(() => {
    const answered = new Set(messages
      .filter((item) => item.kind === "user_message" && item.payload.question_id)
      .map((item) => String(item.payload.question_id)));
    return [...messages].reverse().find((item) =>
      item.kind === "question"
      && Boolean(item.payload.question_id)
      && !answered.has(String(item.payload.question_id)));
  }, [messages]);

  const refresh = async () => {
    await Promise.all([task.refetch(), blackboard.refetch()]);
    setMessages(await tga3Api.getDialogue(taskId));
  };

  return { task, blackboard, messages, pendingQuestion, streamState, refresh };
}
