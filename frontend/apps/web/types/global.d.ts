// CSS 사이드이펙트 import 의 타입 선언.
// next-env.d.ts 는 .gitignore 대상(빌드 시 생성)이라 CI 의 typecheck(빌드 전 실행)에서는
// 존재하지 않는다. 그 결과 `import "@xyflow/react/dist/style.css"` 같은 side-effect import 가
// TS2882 로 실패하므로, 빌드 산출물에 의존하지 않도록 여기서 명시 선언한다.
declare module "*.css";
