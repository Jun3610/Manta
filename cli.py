import asyncio
import sys
import logging
from core.agent import MantaAgent

# 로깅 레벨 조정 (에러 로그만 터미널에 깔끔하게 노출)
logging.basicConfig(level=logging.ERROR)

async def ainput(prompt: str = "") -> str:
    """동기 input()을 비동기 실행하도록 감쌉니다."""
    return await asyncio.to_thread(input, prompt)

async def main():
    print("=" * 60)
    print(" Manta Agent CLI 테스트 인터페이스")
    print(" - 종료하려면 'exit' 또는 'quit'을 입력하세요.")
    print(" - 현재 설정된 LLM 프로바이더로 동작합니다.")
    print("=" * 60)

    try:
        agent = MantaAgent()
    except Exception as e:
        print(f"에이전트 초기화 중 오류 발생: {e}")
        return

    session_id = "cli_session"

    while True:
        try:
            user_input = await ainput("\n사용자 > ")
            user_input = user_input.strip()
            
            if not user_input:
                continue
            
            if user_input.lower() in ["exit", "quit"]:
                print("CLI 테스트를 종료합니다.")
                break
                
            print("Manta > 답변 생성 중...", end="\r", flush=True)
            
            # 독립된 에이전트 레이어에 텍스트 연동
            response = await agent.chat(session_id, user_input)
            
            # 이전 라인 지우기 위해 공백 출력 후 응답 출력
            sys.stdout.write("\r" + " " * 30 + "\r")
            print(f"Manta > {response}")
            
        except KeyboardInterrupt:
            print("\nCLI 테스트를 종료합니다.")
            break
        except Exception as e:
            print(f"\n[오류 발생] {e}")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
