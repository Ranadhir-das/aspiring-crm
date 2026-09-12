from rest_framework.authtoken.models import Token
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.authentication import TokenAuthentication
from rest_framework.permissions import IsAuthenticated


from .serializers import MobileLoginSerializer


class MobileLoginView(APIView):

    def post(self, request):
        serializer = MobileLoginSerializer(
            data=request.data
        )

        serializer.is_valid(raise_exception=True)

        user = serializer.validated_data["user"]

        token, created = Token.objects.get_or_create(
            user=user
        )

        return Response(
            {
                "token": token.key,
                "user": {
                    "id": user.id,
                    "username": user.username,
                    "email": user.email,
                    "name": user.get_full_name(),
                    "role": user.role,
                },
            }
        )



class MobileMeView(APIView):
    authentication_classes = [TokenAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user

        return Response(
            {
                "id": user.id,
                "username": user.username,
                "email": user.email,
                "name": user.get_full_name(),
                "role": user.role,
            }
        )