"use client"

import { DashboardHeader } from "@/components/dashboard-header"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@workspace/ui/components/tabs"
import { OciModelRegistrySettings } from "@/features/settings/oci-model-registry-settings"
import { EmbeddingSettings } from "@/features/settings/embedding-settings"
import { LLMSettings } from "@/features/settings/llm-settings"
import { AssistantSettings } from "@/features/settings/assistant-settings"
import { AuthSettings } from "@/features/settings/auth-settings"
import { CorsSettings } from "@/features/settings/cors-settings"
import { CacheSettings } from "@/features/settings/cache-settings"
import { EmailSettings } from "@/features/settings/email-settings"
import { NotifySettings } from "@/features/settings/notify-settings"
import { ChangeMgmtSettings } from "@/features/settings/change-mgmt-settings"
import { RelationshipSettings } from "@/features/settings/relationship-settings"

export default function SettingsPage() {
  return (
    <>
      <DashboardHeader title="설정" />
      <div className="flex flex-1 flex-col gap-4 p-4">
        <Tabs defaultValue="auth">
          <TabsList>
            <TabsTrigger value="auth">인증</TabsTrigger>
            <TabsTrigger value="cors">CORS</TabsTrigger>
            <TabsTrigger value="oci-model-registry">OCI 모델 레지스트리</TabsTrigger>
            <TabsTrigger value="embedding">임베딩</TabsTrigger>
            <TabsTrigger value="llm">LLM / AI</TabsTrigger>
            <TabsTrigger value="assistant">AI 어시스턴트</TabsTrigger>
            <TabsTrigger value="email">이메일</TabsTrigger>
            <TabsTrigger value="notify">알림</TabsTrigger>
            <TabsTrigger value="change">변경관리</TabsTrigger>
            <TabsTrigger value="relationships">쿼리 분석</TabsTrigger>
            <TabsTrigger value="cache">캐시</TabsTrigger>
          </TabsList>
          <TabsContent value="auth" className="mt-4">
            <AuthSettings />
          </TabsContent>
          <TabsContent value="cors" className="mt-4">
            <CorsSettings />
          </TabsContent>
          <TabsContent value="oci-model-registry" className="mt-4">
            <OciModelRegistrySettings />
          </TabsContent>
          <TabsContent value="embedding" className="mt-4">
            <EmbeddingSettings />
          </TabsContent>
          <TabsContent value="llm" className="mt-4">
            <LLMSettings />
          </TabsContent>
          <TabsContent value="assistant" className="mt-4">
            <AssistantSettings />
          </TabsContent>
          <TabsContent value="email" className="mt-4">
            <EmailSettings />
          </TabsContent>
          <TabsContent value="notify" className="mt-4">
            <NotifySettings />
          </TabsContent>
          <TabsContent value="change" className="mt-4">
            <ChangeMgmtSettings />
          </TabsContent>
          <TabsContent value="relationships" className="mt-4">
            <RelationshipSettings />
          </TabsContent>
          <TabsContent value="cache" className="mt-4">
            <CacheSettings />
          </TabsContent>
        </Tabs>
      </div>
    </>
  )
}
