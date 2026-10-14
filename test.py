import os
import requests
import json
import time

# API base URL
BASE_URL = os.environ.get("YTFIN_URL", "http://localhost:4999")

# These tests need login turned off (Settings -> Require login, or YTFIN_AUTH_ENABLED=false).

# Test video URL (short public domain video)
TEST_VIDEO_URL = "https://www.youtube.com/watch?v=BCFAWkS_I8s"

def test_health():
    """Test health check endpoint"""
    print("\n=== Testing Health Check ===")
    try:
        response = requests.get(f"{BASE_URL}/health")
        print(f"Status Code: {response.status_code}")
        print(f"Response: {response.json()}")
        return response.status_code == 200
    except Exception as e:
        print(f"Error: {e}")
        return False


def test_get_resolutions(url):
    """Test GET resolutions endpoint"""
    print("\n=== Testing Get Resolutions ===")
    try:
        response = requests.get(f"{BASE_URL}/resolutions", params={"url": url})
        print(f"Status Code: {response.status_code}")
        result = response.json()
        print(f"Response: {json.dumps(result, indent=2)}")
        
        if result.get('success'):
            return True, result.get('resolutions', [])
        return False, []
    except Exception as e:
        print(f"Error: {e}")
        return False, []


def test_get_fps(url):
    """Test GET fps endpoint"""
    print("\n=== Testing Get Available FPS ===")
    try:
        response = requests.get(f"{BASE_URL}/fps", params={"url": url})
        print(f"Status Code: {response.status_code}")
        result = response.json()
        print(f"Response: {json.dumps(result, indent=2)}")
        
        if result.get('success'):
            return True, result.get('available_fps', [])
        return False, []
    except Exception as e:
        print(f"Error: {e}")
        return False, []


def test_download_video(url, resolution, fps=None, codec='copy'):
    """Test POST download endpoint"""
    fps_str = f" at {fps}fps" if fps else ""
    codec_str = ""
    print(f"\n=== Testing Download Video at {resolution}{fps_str}{codec_str} ===")
    try:
        payload = {
            "url": url,
            "resolution": resolution,
            "codec": codec
        }
        if fps:
            payload["fps"] = fps
            
        response = requests.post(
            f"{BASE_URL}/api/jobs",
            json=payload,
            headers={"Content-Type": "application/json"}
        )
        print(f"Status Code: {response.status_code}")
        result = response.json()
        print(f"Response: {json.dumps(result, indent=2)}")
        return result.get('success', False)
    except Exception as e:
        print(f"Error: {e}")
        return False


def main():
    """Run all tests"""
    print("Starting API Tests...")
    print(f"Testing video: {TEST_VIDEO_URL}")
    
    # Test health
    if not test_health():
        print("\n❌ Health check failed. Make sure the API is running!")
        print("Run: python main.py")
        return
    
    print("✓ Health check passed")
    
    # Test get resolutions
    success, resolutions = test_get_resolutions(TEST_VIDEO_URL)
    if not success:
        print("\n❌ Failed to get resolutions")
        return
    
    print(f"✓ Got resolutions: {resolutions}")
    
    # Test get fps
    success, fps_values = test_get_fps(TEST_VIDEO_URL)
    if not success:
        print("\n❌ Failed to get FPS values")
        return
    
    print(f"✓ Got available FPS: {fps_values}")
    
    if resolutions:
        if test_download_video(TEST_VIDEO_URL, resolutions[0]):
            print("✓ Download queued. Watch progress in the UI; files land in the download folder.")
        else:
            print("❌ Failed to queue download")
    else:
        print("\n❌ No resolutions available to test download")


if __name__ == "__main__":
    main()
